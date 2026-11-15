"""telegram_interface.panel_mirror — a turn typed in the admin panel also lands on Telegram.

Laris wants Telegram and the panel to show the same conversation (2026-11-08). Telegram turns already show in the
panel (it reads the shared L2 history); this sends a panel turn the other way: what Laris typed in the panel, marked as
coming from the panel, then GOAT's reply exactly as a Telegram turn would send it. agent-coord digests are not panel
turns and never pass through here (they stay in the panel's Agents tab).
"""
from __future__ import annotations

from telegram_interface.notify import notify

__all__ = ["mirror_panel_turn", "PANEL_PREFIX"]

PANEL_PREFIX = "🖥 Din panou:\n"


async def mirror_panel_turn(chat_id: str, role: str, text: str) -> None:
    """Send one panel message (``role`` "user" or "assistant") to the Telegram chat ``chat_id``."""
    body = PANEL_PREFIX + text if role == "user" else text
    await notify(chat_id, body)
