"""
telegram_interface.bot — entry point for GOAT 2.0 Telegram bot.
build_app(registry) wires handlers; run_polling() starts daemon + long-polling.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict

from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from config import settings
from memory.config import WORKING_STORAGE_URL
from orchestrator.orchestrator import Orchestrator
from registry.registry import ServiceRegistry
from tools.memory_manager import build_memory_manager_tools
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
        tools=[search_memory, store_memory, promote_memory, *manager_tools, *workflow_tools],
    )

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

    async def drain_archives(application: Application) -> None:
        """post_shutdown: await in-flight L3 archive writes before the loop exits."""
        await orchestrator.drain_archives()

    builder = Application.builder().token(settings.TELEGRAM_BOT_TOKEN)
    if post_init:
        builder = builder.post_init(post_init)
    builder = builder.post_shutdown(drain_archives)
    app = builder.build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    log.info("Telegram bot application built (model=%s)", settings.MODEL_NAME)
    return app


def run_polling() -> None:
    """Build registry, wire the plugin scanner, and start long-polling."""
    from telegram_interface._plugin_scanner import post_init_hook  # cycle-safe: telegram only

    log.info("Starting GOAT 2.0 Telegram bot (model=%s)", settings.MODEL_NAME)
    registry = ServiceRegistry()
    build_app(registry, post_init=post_init_hook(registry)).run_polling()
