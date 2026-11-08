"""tests.test_panel_mirror — a panel turn is sent to Telegram: the typed text marked as from the panel, the reply as is."""
from __future__ import annotations

import asyncio

import telegram

from telegram_interface import panel_mirror


class _Bot:
    sent: list[dict] = []

    def __init__(self, token):
        self.token = token

    async def send_message(self, **kw):
        _Bot.sent.append(kw)


def test_user_text_is_marked_and_the_reply_is_sent_as_is(monkeypatch):
    monkeypatch.setattr(telegram, "Bot", _Bot)
    monkeypatch.setattr("config.settings.TELEGRAM_BOT_TOKEN", "1:t")
    _Bot.sent = []
    asyncio.run(panel_mirror.mirror_panel_turn("1912", "user", "salut"))
    asyncio.run(panel_mirror.mirror_panel_turn("1912", "assistant", "bună"))
    assert [m["chat_id"] for m in _Bot.sent] == [1912, 1912]
    assert _Bot.sent[0]["text"].startswith("🖥 Din panou:") and "salut" in _Bot.sent[0]["text"]
    assert "bună" in _Bot.sent[1]["text"] and "Din panou" not in _Bot.sent[1]["text"]
