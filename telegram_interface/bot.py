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
from memory.config import WORKING_STORAGE_URL
from orchestrator.orchestrator import Orchestrator
from registry.registry import ServiceRegistry
from tools.memory_manager import build_memory_manager_tools
from tools.identity_tool import build_set_identity_tool
from tools.memory_promote import build_promote_memory_tool
from tools.memory_tools import build_search_memory_tool
from tools.memory_writer import build_store_memory_tool
from tools.workflow_tools import build_workflow_tools
from utils.logging.setup import get_logger
from workflow.config import WorkflowConfig
from workflow.dag_channel import DagChannel
from workflow.dag_manager import DagManager
from workflow.routing import AgentRouter
from workflow.runner import WorkflowRunner

log = get_logger(__name__)

_MAX_TG_LEN = 4096
_ERROR_REPLY = "Something went wrong, please try again."


def _truncate(text: str) -> str:
    """Truncate text to Telegram's 4096-character message limit."""
    return text[:_MAX_TG_LEN] if len(text) > _MAX_TG_LEN else text


def _load_admin_chat_id() -> str:
    """Read admin_chat_id from goat2.toml, or empty string if not configured."""
    cfg = _ROOT / "goat2.toml"
    if not cfg.exists():
        return ""
    try:
        with open(cfg, "rb") as f:
            data = tomllib.load(f)
        return str(data.get("interface", {}).get("telegram", {}).get("admin_chat_id", ""))
    except Exception:
        return ""


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


def build_app(registry: ServiceRegistry, *, post_init=None) -> Application:
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
                reply = await orchestrator.run(synthetic, chat_id)
            except Exception:
                log.exception("dag completion orchestrator.run() failed chat=%s dag=%s", chat_id, dag_id)
                return
            if reply:
                try:
                    from telegram import Bot
                    await Bot(token=settings.TELEGRAM_BOT_TOKEN).send_message(
                        chat_id=int(chat_id), text=_truncate(reply)
                    )
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

    admin_chat_id = _load_admin_chat_id()

    def _is_admin(update: Update) -> bool:
        if not admin_chat_id:
            return True  # no admin configured → allow everyone (open instance)
        return str(update.effective_chat.id) == admin_chat_id

    # ── /update ───────────────────────────────────────────────────────────────

    async def handle_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
        text = update.message.text or ""
        chat_id = str(update.effective_chat.id)
        log.info("chat=%s text=%r", chat_id, text[:80])
        async with _chat_locks[chat_id]:
            try:
                reply = await orchestrator.run(text, chat_id)
            except Exception:
                log.exception("orchestrator.run() failed for chat=%s", chat_id)
                reply = _ERROR_REPLY
        if reply:
            await update.message.reply_text(_truncate(reply))

    async def drain_background(application: Application) -> None:
        """post_shutdown: drain orchestrator + layers background tasks before exit."""
        await orchestrator.drain_background()
        await registry.memory_layers.drain_background()

    builder = Application.builder().token(settings.TELEGRAM_BOT_TOKEN)
    if post_init:
        builder = builder.post_init(post_init)
    builder = builder.post_shutdown(drain_background)
    app = builder.build()
    app.add_handler(CommandHandler("update",   handle_update))
    app.add_handler(CommandHandler("rollback", handle_rollback))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    log.info("Telegram bot application built (model=%s)", settings.MODEL_NAME)
    return app


def run_polling() -> None:
    """Build registry, wire the plugin scanner, and start long-polling."""
    from telegram_interface._plugin_scanner import post_init_hook  # cycle-safe: telegram only

    log.info("Starting GOAT 2.0 Telegram bot (model=%s)", settings.MODEL_NAME)
    registry = ServiceRegistry()
    build_app(registry, post_init=post_init_hook(registry)).run_polling()
