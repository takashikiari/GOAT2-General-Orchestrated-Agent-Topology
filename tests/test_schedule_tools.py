"""GOAT's scheduling tools (tools/schedule_tools.py, 2026-11-15): strict 'when', the turn's own chat, cancel."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from brain import brain as b
from brain import scheduler as s
from tools import schedule_tools as st

# 2026-11-15 17:00 in Bucharest (EET, UTC+2)
_NOW = datetime(2026, 11, 15, 17, 0, tzinfo=st.TZ).timestamp()


def _at(ts):
    return datetime.fromtimestamp(ts, st.TZ).strftime("%Y-%m-%d %H:%M")


def test_absolute_today_if_still_ahead_else_tomorrow():
    assert _at(st.parse_when("18:00", _NOW)) == "2026-11-15 18:00"
    assert _at(st.parse_when("9:05", _NOW)) == "2026-11-16 09:05"
    assert _at(st.parse_when("17:00", _NOW)) == "2026-11-16 17:00"        # exactly now has passed → tomorrow


def test_relative_minutes_and_hours():
    assert st.parse_when("+30m", _NOW) == _NOW + 1800
    assert st.parse_when(" +2H ", _NOW) == _NOW + 7200


def test_the_romanian_time_zone_is_used_not_utc():
    utc_hour = datetime.fromtimestamp(st.parse_when("18:00", _NOW), timezone.utc).hour
    assert utc_hour == 16                                                  # 18:00 EET = 16:00 UTC


@pytest.mark.parametrize("bad", ["mâine la 6", "18", "24:00", "18:60", "+0m", "+30s", "30m", "+9999h", "", None])
def test_anything_else_is_refused_with_the_accepted_formats(bad):
    with pytest.raises(ValueError) as e:
        st.parse_when(bad, _NOW)
    assert str(e.value).startswith("❌")


@pytest.fixture
def tools(monkeypatch, tmp_path):
    monkeypatch.setattr(b, "CONFIG", {**b._DEFAULTS, "enabled": True, "scheduler": True,
                                      "db_path": str(tmp_path / "brain.sqlite")})
    monkeypatch.setattr(b, "_instance", None)
    built = {t.name: t.handler for t in st.build_schedule_tools()}
    yield built
    if b._instance is not None:
        b._instance.close()


def test_schedule_list_cancel_in_the_turns_own_chat(tools):
    run = asyncio.run
    ok = run(tools["schedule_message"](when="+30m", text="Raportul e gata?", chat_id="42"))
    assert ok.startswith("✅ Programat #1 pentru ")
    run(tools["schedule_message"](when="+2h", text="altul", chat_id="99"))
    listed = run(tools["list_scheduled"](chat_id="42"))
    assert "#1" in listed and "Raportul e gata?" in listed and "altul" not in listed     # only this chat's
    assert run(tools["cancel_scheduled"](task_id=2, chat_id="42")).startswith("❌")       # another chat's task
    assert run(tools["cancel_scheduled"](task_id=1, chat_id="42")) == "✅ Anulat #1."
    assert run(tools["cancel_scheduled"](task_id=1, chat_id="42")).startswith("❌")       # already cancelled
    assert run(tools["list_scheduled"](chat_id="42")) == "Niciun mesaj programat."
    sent = []

    async def send(c, t):
        sent.append((c, t))
    run(s.run_due(b.get_instance(), send, now=_NOW + 10 ** 9))
    assert sent == [("99", "altul")]                                                     # a cancelled one never goes


def test_bad_input_is_a_clear_error_not_a_crash(tools):
    assert asyncio.run(tools["schedule_message"](when="mâine", text="x", chat_id="42")).startswith("❌ Format")
    assert asyncio.run(tools["schedule_message"](when="+5m", text="  ", chat_id="42")).startswith("❌")
    assert asyncio.run(tools["cancel_scheduled"](task_id="abc", chat_id="42")).startswith("❌")


def test_tools_only_when_brain_and_scheduler_are_on(monkeypatch):
    from telegram_interface import bot
    monkeypatch.setattr(b, "CONFIG", {**b._DEFAULTS, "enabled": True, "scheduler": False})
    assert bot._schedule_tools() == []
    monkeypatch.setattr(b, "CONFIG", {**b._DEFAULTS, "enabled": False, "scheduler": True})
    assert bot._schedule_tools() == []
    monkeypatch.setattr(b, "CONFIG", {**b._DEFAULTS, "enabled": True, "scheduler": True})
    assert [t.name for t in bot._schedule_tools()] == ["schedule_message", "list_scheduled", "cancel_scheduled"]
