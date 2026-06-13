"""GOAT 2.0 Telegram interface — one message = one intent.

Tool calls are executed internally by _call_with_tools() and never exposed
as separate Telegram messages. The bot only responds with result.summary.
"""
from __future__ import annotations

import logging
import os
from typing import Final

from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

from config.registry import ServiceRegistry
from config.toml_loader import load_toml
from supervisor.supervisor import GoatSupervisor
from supervisor.interfaces.content_filter import mask_sensitive

log = logging.getLogger("goat2.supervisor.interfaces")

_TOKEN: Final[str] = os.environ.get("TELEGRAM_TOKEN") or load_toml().channel_str("telegram_token")

# Global ServiceRegistry for all sessions
_registry = ServiceRegistry()

# Set global registry for tool handlers
from tools.registry_accessor import set_registry
set_registry(_registry)

# Per-chat supervisor — each chat keeps its own conversation history.
_sessions: dict[int, GoatSupervisor] = {}


def _supervisor_for(chat_id: int) -> GoatSupervisor:
    """Return the per-chat GoatSupervisor, creating it on first use."""
    if chat_id not in _sessions:
        _sessions[chat_id] = GoatSupervisor(registry=_registry)
    return _sessions[chat_id]


async def _handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route incoming text to GoatSupervisor and reply with the result summary.

    Only processes messages that have text content. Messages containing only
    tool calls (no text) are silently ignored — tool calls are executed
    internally by _call_with_tools() and never exposed as separate messages.
    """
    # ── FIX: Ignore messages without text (e.g. tool calls, media, stickers) ──
    if not update.message:
        return
    if not update.message.text:
        # This is a non-text update (tool call, sticker, photo, etc.)
        # Tool calls are handled internally by _call_with_tools() — never surface them
        log.debug("Ignoring non-text update type=%s", type(update.message).__name__)
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
        # Filter out tool calls from response (both wrapper and individual invoke tags)
        import re
        # Match DSML tags with DIFFERENT content: <｜｜DSML｜｜invoke>...</｜｜DSML｜｜tool_calls>
        # The opening and closing tags have different content (invoke vs tool_calls)
        clean_text = re.sub(r'<\｜｜DSML｜｜[^>]*>.*?</\｜｜DSML｜｜[^>]*>', '', text, flags=re.DOTALL)
        # Also match mixed pairs: <tag1>...</tag2> where content differs
        clean_text = re.sub(r'<\｜｜DSML｜｜\w+>[^<]*</\｜｜DSML｜｜\w+>', '', clean_text, flags=re.DOTALL)
        # Also strip any orphaned opening/closing tags
        clean_text = re.sub(r'<\｜｜DSML｜｜[^>]*>', '', clean_text)
        clean_text = re.sub(r'</\｜｜DSML｜｜[^>]*>', '', clean_text)
        clean_text = clean_text.strip() or text
        # Telegram message limit: 4096 characters
        MAX_TELEGRAM_LEN = 4096
        if len(clean_text) > MAX_TELEGRAM_LEN:
            clean_text = clean_text[:MAX_TELEGRAM_LEN - 3] + "..."
        await update.message.reply_text(clean_text)
    except Exception as exc:
        import traceback
        log.error("chat=%d error: %s\n%s", chat_id, exc, traceback.format_exc())
        await update.message.reply_text(f"[error] {exc}")


def build_app() -> Application:
    """Build and configure the Telegram Application."""
    if not _TOKEN:
        raise RuntimeError("channels.telegram_token is not set in config/goat.toml")
    app = Application.builder().token(_TOKEN).build()
    # ── FIX: Only handle TEXT messages — ignore tool calls, media, etc. ──
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_message))
    return app


def main() -> None:
    """Entry point — start GOAT Telegram bot with long-polling."""
    import os
    os.makedirs("/home/lenovo/workspace/goat2/logs", exist_ok=True)
    file_handler = logging.FileHandler("/home/lenovo/workspace/goat2/logs/goat2.log")
    file_handler.setFormatter(logging.Formatter("%(asctime)s  %(name)-24s  %(levelname)s  %(message)s"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(name)-24s  %(levelname)s  %(message)s",
        handlers=[logging.StreamHandler(), file_handler],
    )
    log.info("GOAT 2.0 Telegram bot starting.")
    # ── FIX: Only poll for text messages, not tool calls or other update types ──
    build_app().run_polling(
        allowed_updates=["message", "edited_message", "callback_query"]
    )


if __name__ == "__main__":
    main()
