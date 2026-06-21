"""
telegram_interface.bot — Telegram bot entry point for GOAT 2.0.

build_app(registry): Wires Orchestrator; recovers context on first message
per chat; force-promotes all chats on shutdown.
run_polling(): Builds registry + PromotionDaemon, starts long-polling.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from config import settings
from utils.logging.setup import get_logger

if TYPE_CHECKING:
    from registry.registry import ServiceRegistry

log = get_logger(__name__)

_MAX_TG_LEN = 4096
_ERROR_REPLY = "Something went wrong, please try again."


def _truncate(text: str) -> str:
    """Truncate text to Telegram's 4096-character message limit."""
    return text[:_MAX_TG_LEN] if len(text) > _MAX_TG_LEN else text


def build_app(registry: ServiceRegistry, *, post_init=None, post_shutdown=None) -> Application:
    """
    Build and return a configured Telegram Application.

    Creates Orchestrator from registry (DI).  A per-process set tracks
    recovered chat_ids; recover_recent_context() runs once per chat on
    first message.  post_init/post_shutdown forwarded to ApplicationBuilder.
    """
    from orchestrator.orchestrator import Orchestrator  # lazy — avoids import cycle
    from memory.recovery import recover_recent_context  # lazy

    _recovered: set[str] = set()
    orchestrator = Orchestrator(registry)

    async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Recover context on first message per chat, then call orchestrator."""
        text = update.message.text or ""
        chat_id = str(update.effective_chat.id)
        log.info("chat=%s text=%r", chat_id, text[:80])
        if chat_id not in _recovered:
            await recover_recent_context(registry.working_memory, registry.episodic_memory, chat_id)
            _recovered.add(chat_id)
        try:
            reply = await orchestrator.run(text, chat_id)
        except Exception:
            log.exception("orchestrator.run() failed for chat=%s", chat_id)
            reply = _ERROR_REPLY
        await update.message.reply_text(_truncate(reply))

    builder = Application.builder().token(settings.TELEGRAM_BOT_TOKEN)
    if post_init:
        builder = builder.post_init(post_init)
    if post_shutdown:
        builder = builder.post_shutdown(post_shutdown)
    app = builder.build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    log.info("Telegram bot application built (model=%s)", settings.MODEL_NAME)
    return app


def run_polling() -> None:
    """Build registry + PromotionDaemon, wire lifecycle hooks, start polling."""
    from registry.registry import ServiceRegistry  # lazy
    from memory.promotion import PromotionDaemon  # lazy

    log.info("Starting GOAT 2.0 Telegram bot (model=%s)", settings.MODEL_NAME)
    registry = ServiceRegistry()
    daemon = PromotionDaemon(registry.working_memory, registry.episodic_memory)

    async def _start(app: Application) -> None:
        await daemon.start()

    async def _stop(app: Application) -> None:
        from memory.recovery import force_promote_all_chats
        await force_promote_all_chats(registry.working_memory, registry.episodic_memory)
        await daemon.stop()

    build_app(registry, post_init=_start, post_shutdown=_stop).run_polling()
