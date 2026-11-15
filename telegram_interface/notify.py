"""telegram_interface.notify — send a message to a Telegram chat from anywhere in the process (Laris, 2026-11-15).

The one helper for messages that do not go out through a handler's Application: a finished DAG, GOAT's reply to an
agent-coord digest, a panel turn mirrored to Telegram, and later scheduled tasks. It replaces the same
``Bot(token) + tg_format.send`` lines that were copied in three places.

``parse_mode=None`` (the default) keeps exactly what those copies did: ``tg_format.send`` renders the text as HTML in
chunks and falls back to plain text if Telegram rejects the HTML. An explicit ``parse_mode`` sends the text as given
in one message. One ``Bot`` per process, created on first use.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from config import settings
from telegram_interface import tg_format

if TYPE_CHECKING:
    from telegram import Bot

__all__ = ["notify"]

_bot: "Bot | None" = None


def _get_bot() -> "Bot":
    global _bot
    if _bot is None:
        from telegram import Bot
        _bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    return _bot


async def notify(chat_id: str | int, text: str, parse_mode: str | None = None) -> None:
    """Send ``text`` to ``chat_id``. Raises what Telegram raises: callers decide whether a failed send matters."""
    bot = _get_bot()
    if parse_mode is not None:
        await bot.send_message(chat_id=int(chat_id), text=text, parse_mode=parse_mode)
        return
    await tg_format.send(lambda t, pm: bot.send_message(chat_id=int(chat_id), text=t, parse_mode=pm), text)
