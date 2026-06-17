"""GOAT 2.0 Telegram interface — one message = one intent.

The bot is a thin adapter: it converts incoming text to a GOAT intent,
runs it through the supervisor, and sends the result.summary back to
the chat. DSML stripping, tool-call filtering, and the GOAT decision
all happen inside the supervisor / goat_call pipeline — the bot
does not parse, reformat, or retry.
"""
from __future__ import annotations

import logging
import os
from typing import Final

from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from config.registry import ServiceRegistry
from config.toml_loader import load_toml
from supervisor.interfaces.content_filter import mask_sensitive
from supervisor.supervisor import GoatSupervisor
from tools.registry_accessor import set_registry

log = logging.getLogger("goat2.supervisor.interfaces")

_TOKEN: Final[str] = os.environ.get("TELEGRAM_TOKEN") or load_toml().channel_str("telegram_token")
_NO_REPLY = "..."  # sent when the supervisor returns an empty summary

_registry = ServiceRegistry()
set_registry(_registry)
_sessions: dict[int, GoatSupervisor] = {}


def _supervisor_for(chat_id: int) -> GoatSupervisor:
    """Return the per-chat GoatSupervisor, creating it on first use."""
    if chat_id not in _sessions:
        _sessions[chat_id] = GoatSupervisor(registry=_registry)
    return _sessions[chat_id]


def _reply_text(text: str) -> str:
    """Final guard: never send an empty or whitespace-only message."""
    cleaned = (text or "").strip()
    return cleaned or _NO_REPLY


async def _handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Forward incoming text to the per-chat supervisor and reply with the summary.

    Non-text updates (stickers, photos, voice notes, etc.) are silently
    ignored — they have no user intent to forward.
    """
    message = update.message
    if message is None or not message.text:
        log.debug("Ignoring non-text or empty message")
        return
    intent = message.text.strip()
    if not intent:
        return

    supervisor = _supervisor_for(message.chat_id)
    log.debug("chat=%d processing intent=%.80s", message.chat_id, intent)
    try:
        result = await supervisor.run(intent)
        await message.reply_text(_reply_text(mask_sensitive(result.summary)))
    except Exception as exc:
        log.exception("chat=%d error processing intent", message.chat_id)
        await message.reply_text(f"[error] {exc}")


async def _finalize_all_sessions() -> None:
    """End-of-process: persist behavior profiles and stop memory daemons."""
    for chat_id, supervisor in _sessions.items():
        try:
            await supervisor.finalize_session()
            log.info("finalize_session: chat=%d done", chat_id)
        except Exception as exc:  # noqa: BLE001 — best-effort shutdown
            log.warning("finalize_session: chat=%d failed: %s", chat_id, exc)


def build_app() -> Application:
    """Build and configure the Telegram Application with one text handler."""
    if not _TOKEN:
        raise RuntimeError("channels.telegram_token is not set in config/goat.toml")
    app = Application.builder().token(_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_message))
    return app


def main() -> None:
    """Start the Telegram bot with long-polling and finalize on exit."""
    log.info("GOAT 2.0 Telegram bot starting.")
    app = build_app()
    try:
        app.run_polling(allowed_updates=["message"])
    finally:
        import asyncio
        asyncio.run(_finalize_all_sessions())


# Programmatic entry point — used by tests and by callers that want to start
# the bot without going through `python -m telegram_bot`. Alias of main().
run_polling = main


if __name__ == "__main__":
    main()
