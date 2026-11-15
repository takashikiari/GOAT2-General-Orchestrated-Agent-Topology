"""telegram_interface.notify — the one helper for messages sent outside a handler's Application (2026-11-15)."""
from __future__ import annotations

import asyncio

import pytest

from telegram_interface import notify as n


class _FakeBot:
    def __init__(self, reject_html=False):
        self.sent, self.reject_html = [], reject_html

    async def send_message(self, chat_id, text, parse_mode=None):
        if self.reject_html and parse_mode == "HTML":
            from telegram.error import BadRequest
            raise BadRequest("can't parse entities")
        self.sent.append((chat_id, text, parse_mode))


@pytest.fixture
def bot(monkeypatch):
    fake = _FakeBot()
    monkeypatch.setattr(n, "_bot", fake)
    return fake


def test_default_is_what_the_three_copies_did_html_via_tg_format(bot):
    asyncio.run(n.notify("1912576407", "**Gata**"))
    assert bot.sent == [(1912576407, "<b>Gata</b>", "HTML")]          # int chat id, HTML render, like before


def test_rejected_html_falls_back_to_plain_text(monkeypatch):
    fake = _FakeBot(reject_html=True)
    monkeypatch.setattr(n, "_bot", fake)
    asyncio.run(n.notify(42, "**Gata**"))
    assert fake.sent == [(42, "Gata", None)]


def test_explicit_parse_mode_sends_the_text_as_given(bot):
    asyncio.run(n.notify("42", "<i>x</i>", parse_mode="HTML"))
    assert bot.sent == [(42, "<i>x</i>", "HTML")]


def test_one_bot_per_process(monkeypatch):
    made = []

    class _Bot:
        def __init__(self, token):
            made.append(token)
    monkeypatch.setattr(n, "_bot", None)
    monkeypatch.setattr("telegram.Bot", _Bot)
    first, second = n._get_bot(), n._get_bot()
    assert first is second and len(made) == 1


def test_errors_reach_the_caller(monkeypatch):
    class _Down:
        async def send_message(self, **kw):
            raise RuntimeError("network down")
    monkeypatch.setattr(n, "_bot", _Down())
    with pytest.raises(RuntimeError):
        asyncio.run(n.notify(42, "x", parse_mode=None))


def test_bot_py_sends_through_notify_not_its_own_bot():
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[1].joinpath("telegram_interface", "bot.py").read_text()
    assert "Bot(token=" not in src                       # the two copies (DAG, agent-coord reply) are gone
    assert src.count("await notify(") == 2
