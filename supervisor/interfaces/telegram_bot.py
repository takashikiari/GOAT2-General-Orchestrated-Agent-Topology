"""GOAT 2.0 Telegram interface — one message = one intent."""
from __future__ import annotations

import logging
import os
from typing import Final

from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

from config.toml_loader import load_toml
from memory.memory_manager import memory_manager
from supervisor.supervisor import GoatSupervisor
from supervisor.interfaces.content_filter import mask_sensitive

log = logging.getLogger("goat2.telegram")

_TOKEN: Final[str] = os.environ.get("TELEGRAM_TOKEN") or load_toml().channel_str("telegram_token")

# Per-chat supervisor — each chat keeps its own conversation history.
_sessions: dict[int, GoatSupervisor] = {}


def _supervisor_for(chat_id: int) -> GoatSupervisor:
    """Return the per-chat GoatSupervisor, creating it on first use."""
    if chat_id not in _sessions:
        _sessions[chat_id] = GoatSupervisor(memory_manager=memory_manager)
    return _sessions[chat_id]


async def _handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route incoming text to GoatSupervisor and reply with the result summary."""
    if not update.message or not update.message.text:
        return
    intent = update.message.text.strip()
    if not intent:
        return
    chat_id = update.message.chat_id
    sv = _supervisor_for(chat_id)
    try:
        result = await sv.run(intent)
        text = mask_sensitive(result.summary.strip())
        if not text:
            text = "DAG returned empty result. Unverified."
        await update.message.reply_text(text)
    except Exception as exc:
        log.error("chat=%d error: %s", chat_id, exc)
        await update.message.reply_text(f"[error] {exc}")


def build_app() -> Application:
    """Build and configure the Telegram Application."""
    if not _TOKEN:
        raise RuntimeError("channels.telegram_token is not set in config/goat.toml")
    app = Application.builder().token(_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_message))
    return app


def main() -> None:
    """Entry point — start GOAT Telegram bot with long-polling."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(name)-24s  %(levelname)s  %(message)s",
    )
    log.info("GOAT 2.0 Telegram bot starting.")
    build_app().run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
