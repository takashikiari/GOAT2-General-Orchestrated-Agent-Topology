"""
telegram_interface.bot — Telegram bot entry point for GOAT 2.0.

build_app(registry): Wires an Orchestrator to a Telegram Application.
    State lives entirely in WorkingMemory (Redis) — no in-memory caching.
    Each message calls orchestrator.run(text, chat_id); Redis is the only
    source of truth.

run_polling(): Builds a ServiceRegistry, calls build_app(), starts polling.
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


def build_app(registry: ServiceRegistry) -> Application:
    """
    Build and return a configured Telegram Application.

    Creates an Orchestrator from registry (DI — not a module-level global).
    Conversation state is owned entirely by WorkingMemory; the handler passes
    the Telegram chat_id string on every call and does no local caching.

    Args:
        registry: ServiceRegistry holding the LLM client and working memory.
    """
    from orchestrator.orchestrator import Orchestrator  # lazy — avoids import cycle

    orchestrator = Orchestrator(registry)

    async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Pass text and chat_id to orchestrator; send reply."""
        text = update.message.text or ""
        chat_id = str(update.effective_chat.id)
        log.info("chat=%s text=%r", chat_id, text[:80])
        try:
            reply = await orchestrator.run(text, chat_id)
        except Exception:
            log.exception("orchestrator.run() failed for chat=%s", chat_id)
            reply = _ERROR_REPLY
        await update.message.reply_text(_truncate(reply))

    app = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    log.info("Telegram bot application built (model=%s)", settings.MODEL_NAME)
    return app


def run_polling() -> None:
    """
    Build a ServiceRegistry, construct the Telegram app, and start polling.

    Blocking call — returns only when the process is interrupted.
    """
    from registry.registry import ServiceRegistry  # lazy — keeps top-level import-free

    log.info("Starting GOAT 2.0 Telegram bot (model=%s)", settings.MODEL_NAME)
    registry = ServiceRegistry()
    app = build_app(registry)
    app.run_polling()
