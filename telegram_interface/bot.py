"""
telegram_interface.bot — entry point for GOAT 2.0 Telegram bot.
build_app(registry) wires handlers; run_polling() starts daemon + long-polling.
"""
from __future__ import annotations

from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from config import settings
from orchestrator.orchestrator import Orchestrator
from registry.registry import ServiceRegistry
from tools.memory_promote import build_promote_memory_tool
from tools.memory_tools import build_search_memory_tool
from tools.memory_writer import build_store_memory_tool
from utils.logging.setup import get_logger

log = get_logger(__name__)

_MAX_TG_LEN = 4096
_ERROR_REPLY = "Something went wrong, please try again."


def _truncate(text: str) -> str:
    """Truncate text to Telegram's 4096-character message limit."""
    return text[:_MAX_TG_LEN] if len(text) > _MAX_TG_LEN else text


def build_app(registry: ServiceRegistry, *, post_init=None) -> Application:
    """Build and return a configured Telegram Application.

    Creates the Orchestrator with direct dependency references (not the
    registry itself) and routes incoming text messages to it.  Memory is
    accessed entirely through the Orchestrator → MemoryLayers path; this
    module never touches the physical tiers directly.  A ``post_shutdown``
    hook drains in-flight L3 archive writes on clean shutdown.
    """
    layers = registry.memory_layers
    search_memory = build_search_memory_tool(layers)
    store_memory = build_store_memory_tool(layers)
    promote_memory = build_promote_memory_tool(layers)
    orchestrator = Orchestrator(
        layers=layers,
        llm_client=registry.llm_client,
        plugin_manager=registry.plugin_manager,
        analytics=registry.memory_analytics,
        tools=[search_memory, store_memory, promote_memory],
    )

    async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Forward the incoming text to the orchestrator and reply."""
        text = update.message.text or ""
        chat_id = str(update.effective_chat.id)
        log.info("chat=%s text=%r", chat_id, text[:80])
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
