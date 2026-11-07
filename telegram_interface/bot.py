"""
telegram_interface.bot — entry point for GOAT 2.0 Telegram bot.
build_app(registry) wires handlers; run_polling() starts daemon + long-polling.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tomllib
from collections import defaultdict
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

_ROOT = Path(__file__).parent.parent

from config import settings
from config.admin_chat import load_admin_chat_id
from memory.config import WORKING_STORAGE_URL
from orchestrator.orchestrator import Orchestrator
from registry.registry import ServiceRegistry
from memory import usage_probe
from telegram_interface import tg_format
from tools.memory_manager import build_memory_manager_tools
from tools.identity_tool import build_set_identity_tool
from tools.memory_promote import build_promote_memory_tool
from tools.memory_tools import build_search_memory_tool
from tools.memory_writer import build_store_memory_tool
from tools.workflow_tools import build_workflow_tools
from telegram_interface.telegram_config import UPDATE_DEDUPE_TTL_SECONDS as _UPDATE_DEDUPE_TTL_SECONDS
from utils.logging.setup import get_logger
from workflow.config import WorkflowConfig
from workflow.dag_channel import DagChannel
from workflow.dag_manager import DagManager
from workflow.routing import AgentRouter
from workflow.runner import WorkflowRunner

log = get_logger(__name__)

_ERROR_REPLY = "Something went wrong, please try again."


def _current_version() -> str:
    try:
        sys.path.insert(0, str(_ROOT))
        from version import __version__
        return __version__
    except Exception:
        return "unknown"


async def _git_run(*args: str) -> subprocess.CompletedProcess:
    return await asyncio.to_thread(
        lambda: subprocess.run(["git"] + list(args), cwd=_ROOT, capture_output=True, text=True)
    )


async def _pip_install() -> subprocess.CompletedProcess:
    return await asyncio.to_thread(
        lambda: subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", "-r", str(_ROOT / "requirements.txt")],
            cwd=_ROOT, capture_output=True, text=True,
        )
    )


def _check_update_sync() -> dict | None:
    """Synchronous GitHub release check — run via asyncio.to_thread."""
    try:
        cfg_path = _ROOT / "goat2.toml"
        repo = ""
        channel = "stable"
        if cfg_path.exists():
            with open(cfg_path, "rb") as f:
                cfg = tomllib.load(f)
            repo    = cfg.get("updates", {}).get("github_repo", "")
            channel = cfg.get("updates", {}).get("channel", "stable")
        if not repo:
            return None
        import urllib.request, json as _json
        url = f"https://api.github.com/repos/{repo}/releases/latest"
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=8) as r:
            release = _json.loads(r.read())
        remote = release.get("tag_name", "v0.0.0").lstrip("v")
        def _parts(v: str) -> tuple:
            try: return tuple(int(x) for x in v.split(".")[:3])
            except ValueError: return (0, 0, 0)
        if _parts(remote) > _parts(_current_version()):
            return release
        return None
    except Exception:
        return None


def _pick_dag_summary(result: dict) -> str:
    """Return the best single-node output to forward to GOAT as DAG summary.

    Priority: any node whose id contains 'summarizer', then the last result
    value, then empty string.
    """
    results: dict = result.get("results") or {}
    if not results:
        return ""
    for nid, val in results.items():
        if "summarizer" in nid.lower():
            return str(val)
    return str(next(reversed(results.values())))


def build_app(registry: ServiceRegistry, *, post_init=None, post_shutdown=None) -> Application:
    """Build and return a configured Telegram Application.

    Creates the Orchestrator with direct dependency references (not the
    registry itself) and routes incoming text messages to it.  Memory is
    accessed entirely through the Orchestrator → MemoryLayers path; this
    module never touches the physical tiers directly.  A ``post_shutdown``
    hook drains in-flight L3 archive writes on clean shutdown.

    Per-chat asyncio locks (``_chat_locks``) serialize all traffic into the
    orchestrator for a given chat_id — both live user messages and DAG
    completion notifications — so GOAT is never called concurrently for the
    same chat.  DAG completion waits for the lock without a timeout; the lock
    is always released (``async with`` guarantees this even on exception) so
    there is no deadlock risk.
    """
    layers = registry.memory_layers

    # One asyncio.Lock per chat_id — created lazily on first access.
    _chat_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    # Memory tools
    search_memory = build_search_memory_tool(layers)
    store_memory = build_store_memory_tool(layers)
    promote_memory = build_promote_memory_tool(layers)
    set_identity = build_set_identity_tool(layers)
    manager_tools = build_memory_manager_tools(layers)

    # Workflow tools — DagManager is scoped to this app instance (no singleton)
    wf_config = WorkflowConfig.from_toml(redis_url=WORKING_STORAGE_URL)
    wf_runner = WorkflowRunner(
        max_concurrent=wf_config.max_concurrent_nodes,
        node_timeout=wf_config.node_timeout_seconds,
    )

    def _channel_factory(dag_id: str) -> DagChannel:
        return DagChannel(
            wf_config.redis_url, dag_id,
            prefix=wf_config.dag_key_prefix,
            ttl=wf_config.dag_ttl_seconds,
        )

    agent_router = AgentRouter()

    async def _on_dag_complete(dag_id: str, chat_id: str, state: str, result: dict) -> None:
        """Route DAG completion through GOAT so only GOAT speaks to the user.

        Waits for the per-chat lock (no timeout — the lock is always released
        after each message handler turn) then calls orchestrator.run() with a
        synthetic notification message.  GOAT reads the workflow summary and
        composes the reply; the bot sends it normally.
        """
        if not chat_id:
            return
        summary = _pick_dag_summary(result)
        errors = result.get("errors") or {}
        state_label = "completed successfully" if state == "done" else f"finished with state: {state}"
        parts = [f"[Workflow '{dag_id}' {state_label}]"]
        if summary:
            parts.append(summary[:3000])
        if errors:
            err_lines = [f"{nid}: {err}" for nid, err in errors.items()]
            parts.append("Errors:\n" + "\n".join(err_lines))
        synthetic = "\n\n".join(parts)

        log.info("dag_complete: waiting for chat lock chat=%s dag=%s", chat_id, dag_id)
        async with _chat_locks[chat_id]:
            log.info("dag_complete: lock acquired chat=%s dag=%s; running orchestrator", chat_id, dag_id)
            try:
                injected: list = []
                reply = await orchestrator.run(synthetic, chat_id, on_context_assembled=injected.append)
                usage_probe.log_usage(chat_id, injected[-1] if injected else None, reply or "")
            except Exception:
                log.exception("dag completion orchestrator.run() failed chat=%s dag=%s", chat_id, dag_id)
                return
            if reply:
                try:
                    from telegram import Bot
                    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
                    await tg_format.send(
                        lambda t, pm: bot.send_message(chat_id=int(chat_id), text=t, parse_mode=pm), reply)
                except Exception as exc:
                    log.warning("dag completion send failed chat=%s: %s", chat_id, exc)

    dag_manager = DagManager(wf_runner, _channel_factory, router=agent_router, on_complete=_on_dag_complete)
    workflow_tools = build_workflow_tools(dag_manager, _channel_factory)

    orchestrator = Orchestrator(
        layers=layers,
        llm_client=registry.llm_client,
        plugin_manager=registry.plugin_manager,
        analytics=registry.memory_analytics,
        tools=[search_memory, store_memory, promote_memory, set_identity, *manager_tools, *workflow_tools],
    )

    admin_chat_id = load_admin_chat_id()

    async def _on_coord_messages(messages: list[dict]) -> None:
        """Route new PetVoyageUK agent-coord messages through GOAT's own
        orchestrator loop instead of a raw Telegram push - mirrors
        _on_dag_complete above. Without this, GOAT never actually "read" or
        remembered coordination activity; it only knew about it if asked
        directly (2026-08-25 finding)."""
        if not admin_chat_id:
            return
        from telegram_interface._coord_poller import format_digest, retrieval_text
        synthetic = format_digest(messages)
        async with _chat_locks[admin_chat_id]:
            try:
                injected: list = []
                reply = await orchestrator.run(synthetic, admin_chat_id, on_context_assembled=injected.append,
                                               retrieval_query=retrieval_text(messages))
                usage_probe.log_usage(admin_chat_id, injected[-1] if injected else None, reply or "")
            except Exception:
                log.exception("coord_poller orchestrator.run() failed")
                reply = ""
        # orchestrator.run() can return an empty reply (e.g. a tool round
        # that ran out of budget mid-synthesis - see the 2026-08-24 incident
        # noted on handle_message below). Falling back to the raw digest
        # here, instead of silently sending nothing, is what the admin sees
        # as "GOAT gives fallback / doesn't respond" - the admin must always
        # see that new coordination activity happened, even when GOAT's own
        # reasoning about it failed (2026-08-25 regression, caught same day).
        if not reply or not reply.strip():
            log.warning("coord_poller: empty reply from orchestrator, falling back to raw digest")
            reply = synthetic
        try:
            from telegram import Bot
            bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
            await tg_format.send(
                lambda t, pm: bot.send_message(chat_id=int(admin_chat_id), text=t, parse_mode=pm), reply)
        except Exception as exc:
            log.warning("coord_poller send failed: %s", exc)

    def _is_admin(update: Update) -> bool:
        if not admin_chat_id:
            return True  # no admin configured → allow everyone (open instance)
        return str(update.effective_chat.id) == admin_chat_id

    async def _is_duplicate_update(update: Update) -> bool:
        """Redelivery guard: True if this Telegram ``update_id`` was already handled.

        Telegram redelivers an update if the bot crashes/restarts before the
        long-poll cycle that received it completes — the handler then reruns
        end-to-end (duplicate L3 archive write, duplicate reply, duplicate
        admin action). ``SET NX EX`` is atomic, so concurrent handling of the
        same update_id (should it ever happen) still only "wins" once.
        """
        client = registry.working_memory._get_client()
        key = f"tg:seen_update:{update.update_id}"
        first_time = await client.set(key, "1", nx=True, ex=_UPDATE_DEDUPE_TTL_SECONDS)
        return not first_time

    # ── /update ───────────────────────────────────────────────────────────────

    async def handle_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await _is_duplicate_update(update):
            log.warning("duplicate update_id=%s skipped (redelivery)", update.update_id)
            return
        if not _is_admin(update):
            await update.message.reply_text("⛔ This command is restricted to the bot admin.")
            return

        msg = await update.message.reply_text("🔍 Checking for updates...")
        release = await asyncio.to_thread(_check_update_sync)
        current = _current_version()

        if release is None:
            await msg.edit_text(f"✅ GOAT is up to date (v{current}).")
            return

        remote_tag  = release.get("tag_name", "?")
        changelog   = (release.get("body") or "No changelog provided.").strip()
        if len(changelog) > 800:
            changelog = changelog[:800] + "…"
        release_url = release.get("html_url", "")

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Update now", callback_data="update:confirm"),
            InlineKeyboardButton("❌ Cancel",     callback_data="update:cancel"),
        ]])
        text = (
            f"🆕 *New version available: {remote_tag}*\n"
            f"Current: v{current}\n\n"
            f"*What's new:*\n{changelog}\n\n"
            f"[View on GitHub]({release_url})"
        )
        await msg.edit_text(text, reply_markup=keyboard, parse_mode="Markdown",
                            disable_web_page_preview=True)

    # ── /rollback ─────────────────────────────────────────────────────────────

    async def handle_rollback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await _is_duplicate_update(update):
            log.warning("duplicate update_id=%s skipped (redelivery)", update.update_id)
            return
        if not _is_admin(update):
            await update.message.reply_text("⛔ This command is restricted to the bot admin.")
            return

        result = await _git_run("tag", "--sort=-version:refname")
        tags = [t for t in result.stdout.strip().splitlines() if t.startswith("v")][:6]
        current = _current_version()

        if not tags:
            await update.message.reply_text("No release tags found in the repository.")
            return

        buttons = [
            [InlineKeyboardButton(
                f"{'▶ ' if t == f'v{current}' else ''}{t}",
                callback_data=f"rollback:select:{t}",
            )]
            for t in tags
        ]
        buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="rollback:cancel")])
        await update.message.reply_text(
            f"Current version: *v{current}*\nSelect a version to restore:",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="Markdown",
        )

    # ── inline keyboard callbacks ─────────────────────────────────────────────

    async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_admin(update):
            log.warning("ignored %s from non-admin chat=%s", "callback", update.effective_chat.id if update.effective_chat else "?")
            return
        if await _is_duplicate_update(update):
            log.warning("duplicate update_id=%s skipped (redelivery)", update.update_id)
            return
        query = update.callback_query
        await query.answer()
        data = query.data or ""

        # ── update flow ───────────────────────────────────────────────────────
        if data == "update:confirm":
            await query.edit_message_text("⏳ Pulling latest code...")
            pull = await _git_run("pull", "--ff-only")
            if pull.returncode != 0:
                await query.edit_message_text(
                    f"❌ *git pull failed:*\n```{pull.stderr[:500]}```",
                    parse_mode="Markdown",
                )
                return

            await query.edit_message_text("⏳ Installing dependencies...")
            pip = await _pip_install()
            if pip.returncode != 0:
                await query.edit_message_text(
                    f"❌ *pip install failed:*\n```{pip.stderr[:500]}```",
                    parse_mode="Markdown",
                )
                return

            await query.edit_message_text("✅ Update installed. Restarting GOAT…")
            await asyncio.sleep(1)
            os.execv(sys.executable, [sys.executable] + sys.argv)

        elif data == "update:cancel":
            await query.edit_message_text("Update cancelled.")

        # ── rollback flow ─────────────────────────────────────────────────────
        elif data.startswith("rollback:select:"):
            tag = data.split(":", 2)[2]
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton(f"✅ Confirm — restore {tag}", callback_data=f"rollback:confirm:{tag}"),
                InlineKeyboardButton("❌ Cancel", callback_data="rollback:cancel"),
            ]])
            await query.edit_message_text(
                f"⚠️ Roll back to *{tag}*?\n\nGOAT will restart on the older version.",
                reply_markup=keyboard,
                parse_mode="Markdown",
            )

        elif data.startswith("rollback:confirm:"):
            tag = data.split(":", 2)[2]
            await query.edit_message_text(f"⏳ Checking out {tag}…")

            await _git_run("stash", "--include-untracked")
            checkout = await _git_run("checkout", tag)
            if checkout.returncode != 0:
                await query.edit_message_text(
                    f"❌ *git checkout failed:*\n```{checkout.stderr[:500]}```",
                    parse_mode="Markdown",
                )
                return

            await query.edit_message_text(f"⏳ Reinstalling dependencies for {tag}…")
            pip = await _pip_install()
            if pip.returncode != 0:
                await query.edit_message_text(
                    f"❌ *pip install failed:*\n```{pip.stderr[:500]}```",
                    parse_mode="Markdown",
                )
                return

            await query.edit_message_text(f"✅ Rolled back to *{tag}*. Restarting GOAT…",
                                          parse_mode="Markdown")
            await asyncio.sleep(1)
            os.execv(sys.executable, [sys.executable] + sys.argv)

        elif data == "rollback:cancel":
            await query.edit_message_text("Rollback cancelled.")

    # ── message handler ───────────────────────────────────────────────────────

    async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Forward the incoming text to the orchestrator and reply."""
        if not _is_admin(update):
            log.warning("ignored %s from non-admin chat=%s", "message", update.effective_chat.id if update.effective_chat else "?")
            return
        if await _is_duplicate_update(update):
            log.warning("duplicate update_id=%s skipped (redelivery)", update.update_id)
            return
        text = update.message.text or ""
        chat_id = str(update.effective_chat.id)
        log.info("chat=%s text=%r", chat_id, text[:80])
        async with _chat_locks[chat_id]:
            try:
                injected: list = []
                reply = await orchestrator.run(text, chat_id, on_context_assembled=injected.append)
                usage_probe.log_usage(chat_id, injected[-1] if injected else None, reply or "")
            except Exception:
                log.exception("orchestrator.run() failed for chat=%s", chat_id)
                reply = _ERROR_REPLY
        # orchestrator.run() now guards its own empty-reply case, but this is
        # the last line of defense before Telegram: `if reply:` used to mean
        # an empty string here (2026-08-24 incident: a tool round terminated
        # with blank model output) silently sent nothing at all - no message,
        # no error, no log - the user just waited forever with no signal the
        # turn had even completed. Always send something.
        if not reply or not reply.strip():
            log.warning("handle_message: empty reply reached telegram layer chat=%s", chat_id)
            reply = _ERROR_REPLY
        await tg_format.send(lambda t, pm: update.message.reply_text(t, parse_mode=pm), reply)

    # Coord poller is wired here, not in run_polling(), because its
    # on_messages callback (_on_coord_messages above) needs closure access
    # to `orchestrator` and `_chat_locks` — both created inside build_app —
    # the same reason DagManager's on_complete is wired here too.
    from telegram_interface._coord_poller import post_init_hook as _coord_hook
    coord_post_init, coord_post_shutdown = _coord_hook(registry, _on_coord_messages)

    async def _combined_post_init(application: Application) -> None:
        if post_init:
            await post_init(application)
        await coord_post_init(application)

    async def drain_background(application: Application) -> None:
        """post_shutdown: drain orchestrator + layers background tasks, then
        run the caller's own post_shutdown (e.g. cancelling the plugin
        scanner / admin panel / tunnel tasks) — PTB only takes one
        post_shutdown callback, so this is where the two get chained.
        """
        await orchestrator.drain_background()
        await registry.memory_layers.drain_background()
        await coord_post_shutdown(application)
        if post_shutdown:
            await post_shutdown(application)

    builder = Application.builder().token(settings.TELEGRAM_BOT_TOKEN)
    builder = builder.post_init(_combined_post_init)
    builder = builder.post_shutdown(drain_background)
    app = builder.build()
    app.add_handler(CommandHandler("update",   handle_update))
    app.add_handler(CommandHandler("rollback", handle_rollback))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    log.info("Telegram bot application built (model=%s)", settings.MODEL_NAME)
    return app


def run_polling() -> None:
    """Build registry, wire the plugin scanner, and start long-polling.
    The coord poller is wired inside build_app itself (needs closure access
    to the orchestrator - see the comment above drain_background there)."""
    from telegram_interface._plugin_scanner import post_init_hook as _plugin_hook  # cycle-safe: telegram only

    log.info("Starting GOAT 2.0 Telegram bot (model=%s)", settings.MODEL_NAME)
    registry = ServiceRegistry()
    plugin_post_init, plugin_post_shutdown = _plugin_hook(registry)

    build_app(registry, post_init=plugin_post_init, post_shutdown=plugin_post_shutdown).run_polling()
