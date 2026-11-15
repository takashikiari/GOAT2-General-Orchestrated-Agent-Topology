"""The memory brain's sleep loop started with the bot (telegram_interface/_brain_sleep.py, 2026-11-15)."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from types import SimpleNamespace

import pytest

from brain import brain as b
from telegram_interface import _brain_sleep


class _Working:
    def __init__(self, chats=None):
        self.chats = chats or {}

    async def get_messages(self, chat_id):
        return list(self.chats.get(chat_id, []))


@pytest.fixture
def brain_on(monkeypatch, tmp_path):
    monkeypatch.setattr(b, "CONFIG", {**b._DEFAULTS, "enabled": True, "db_path": str(tmp_path / "brain.sqlite"),
                                      "interval_seconds": 3600})
    monkeypatch.setattr(b, "_instance", None)
    monkeypatch.setattr(b, "_sleep", None)
    yield tmp_path / "brain.sqlite"
    if b._instance is not None:
        b._instance.close()


def _run_hook(registry, chat_id):
    post_init, post_shutdown = _brain_sleep.post_init_hook(registry, chat_id)

    async def go():
        await post_init(None)
        await asyncio.sleep(0.05)            # let the first sleep cycle run
        await post_shutdown(None)
    asyncio.run(go())


def test_disabled_starts_nothing(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(b, "CONFIG", {**b._DEFAULTS, "enabled": False, "db_path": str(tmp_path / "x.sqlite")})
    monkeypatch.setattr(b, "start", lambda *a, **kw: calls.append(a))
    _run_hook(SimpleNamespace(working_memory=_Working()), "42")
    assert calls == [] and not (tmp_path / "x.sqlite").exists()


def test_enabled_runs_a_real_cycle_and_stops_cleanly(brain_on):
    chat = [{"role": "user", "content": "Cum stăm cu wizard-ul?", "timestamp": 10.0}]
    _run_hook(SimpleNamespace(working_memory=_Working({"42": chat})), "42")
    con = sqlite3.connect(brain_on)
    assert con.execute("SELECT count(*) FROM cycles").fetchone()[0] >= 1      # the loop ran at startup
    assert con.execute("SELECT count(*) FROM events").fetchone()[0] == 1
    con.close()


def test_no_admin_chat_or_a_failing_start_never_blocks_the_bot(monkeypatch, brain_on):
    _run_hook(SimpleNamespace(working_memory=_Working()), "")                 # no chat: skipped, no error

    def boom(*a, **kw):
        raise RuntimeError("store unavailable")
    monkeypatch.setattr(b, "start", boom)
    _run_hook(SimpleNamespace(working_memory=_Working()), "42")               # logged, never raised


def test_hook_and_sleep_share_one_brain(brain_on):
    async def go():
        await b.record_turn("42", [{"role": "user", "content": "x", "timestamp": 1.0}])
        task = b.start(_Working(), ["42"])
        await asyncio.sleep(0.05)
        task.cancel()
    asyncio.run(go())
    assert b._instance is b.get_instance()


def test_the_nucleus_sees_turns_the_hook_wrote(brain_on):
    """The hook stores the turn first, so the sleep finds nothing new in L2; the nucleus must still refresh."""
    turn = [{"role": "user", "content": "Și ce facem cu wizard-ul?", "timestamp": 100.0}]

    async def go():
        await b.record_turn("42", turn, lane="human")
        await b.get_instance().sleep_once(_Working({"42": turn}), ["42"])   # nothing new for the sleep in L2
        return await b.get_instance().nucleus("42", "human")
    items = asyncio.run(go())
    assert [i["kind"] for i in items] == ["unanswered"]
    assert "wizard" in json.dumps(items, ensure_ascii=False)


def test_start_is_idempotent(brain_on, monkeypatch):
    monkeypatch.setattr(b, "_sleep", None)

    async def go():
        t1 = b.start(_Working(), ["42"])
        t2 = b.start(_Working(), ["42"])
        same = t1 is t2
        await b.stop_sleep()
        return same, t1.done()
    same, done = asyncio.run(go())
    assert same is True                      # a second start returns the running loop, never a second one
    assert done is True


def test_shutdown_waits_for_the_cycle_in_progress(brain_on, monkeypatch):
    """stop_sleep lets a running cycle finish (no write cut, no 'Task was destroyed but it is pending')."""
    monkeypatch.setattr(b, "_sleep", None)
    finished = []
    real = b.get_instance().sleep_once

    async def slow_cycle(working, chat_ids):
        await asyncio.sleep(0.3)             # a cycle that is still running when shutdown begins
        r = await real(working, chat_ids)
        finished.append(r)
        return r
    monkeypatch.setattr(b.get_instance(), "sleep_once", slow_cycle)

    async def go():
        task = b.start(_Working(), ["42"])
        await asyncio.sleep(0.05)            # shutdown arrives in the middle of the first cycle
        await b.stop_sleep()
        return task
    task = asyncio.run(go())
    assert len(finished) == 1                # the cycle in progress completed
    assert task.done() and not task.cancelled()
