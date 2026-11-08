"""telegram_interface.panel_mirror — a turn typed in the admin panel also lands on Telegram.

Laris wants Telegram and the panel to show the same conversation (2026-11-08). Telegram turns already show in the
panel (it reads the shared L2 history); this sends a panel turn the other way: what Laris typed in the panel, marked as
coming from the panel, then GOAT's reply exactly as a Telegram turn would send it. agent-coord digests are not panel
turns and never pass through here (they stay in the panel's Agents tab).
"""
from __future__ import annotations

from config import settings
from telegram_interface import tg_format

__all__ = ["mirror_panel_turn", "PANEL_PREFIX"]

PANEL_PREFIX = "🖥 Din panou:\n"


async def mirror_panel_turn(chat_id: str, role: str, text: str) -> None:
    """Send one panel message (``role`` "user" or "assistant") to the Telegram chat ``chat_id``."""
    from telegram import Bot
    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    body = PANEL_PREFIX + text if role == "user" else text
    await tg_format.send(lambda t, pm: bot.send_message(chat_id=int(chat_id), text=t, parse_mode=pm), body)
