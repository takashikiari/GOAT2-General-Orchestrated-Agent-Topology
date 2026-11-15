"""Scheduled messages on the brain's tick (brain/scheduler.py + Brain.run_forever on_tick + _brain_sleep, 2026-11-15).

- a due task is sent once through the given send(), a future one waits; a past run_at goes out at the next tick;
- a failing send is retried on later ticks, then marked failed after max_attempts (kept, never deleted);
- one bad task never blocks the others;
- tasks survive a restart (a new Brain on the same file sees them); a task left 'sending' is recovered;
- the sleep loop runs on_tick after each cycle, and a failing on_tick never stops the loop;
- end to end: the bot's post_init starts the loop with the scheduler, and a due task goes out through notify().
"""
from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace

import pytest

from brain import brain as b
from brain import scheduler as s
from brain.store import Brain
from telegram_interface import _brain_sleep


def _brain(tmp_path):
    return Brain({**b._DEFAULTS, "enabled": True, "db_path": str(tmp_path / "brain.sqlite")})


class _Send:
    def __init__(self, fail_for=()):
        self.sent, self.fail_for = [], set(fail_for)

    async def __call__(self, chat_id, text):
        if text in self.fail_for:
            raise RuntimeError("telegram down")
        self.sent.append((chat_id, text))


def _status(tmp_path):
    con = sqlite3.connect(tmp_path / "brain.sqlite")
    try:
        return con.execute("SELECT text, status, attempts FROM scheduled_tasks ORDER BY id").fetchall()
    finally:
        con.close()


def test_a_due_task_goes_out_once_and_a_future_one_waits(tmp_path):
    brain, send = _brain(tmp_path), _Send()
    try:
        asyncio.run(s.schedule(brain, 42, 1000.0, "acum"))
        asyncio.run(s.schedule(brain, 42, 5000.0, "mai târziu"))
        first = asyncio.run(s.run_due(brain, send, now=1030.0))
        again = asyncio.run(s.run_due(brain, send, now=1090.0))
        later = asyncio.run(s.run_due(brain, send, now=5010.0))
    finally:
        brain.close()
    assert send.sent == [("42", "acum"), ("42", "mai târziu")]
    assert (first["due"], first["sent"], again["due"], later["sent"]) == (1, 1, 0, 1)
    assert _status(tmp_path) == [("acum", "sent", 1), ("mai târziu", "sent", 1)]


def test_a_failing_send_is_retried_then_marked_failed_and_kept(tmp_path):
    brain, send = _brain(tmp_path), _Send(fail_for={"rău"})
    try:
        asyncio.run(s.schedule(brain, 42, 100.0, "rău"))
        asyncio.run(s.schedule(brain, 42, 100.0, "bun"))
        ticks = [asyncio.run(s.run_due(brain, send, now=200.0 + i * 60, max_attempts=3)) for i in range(4)]
    finally:
        brain.close()
    assert send.sent == [("42", "bun")]                                   # the bad one never blocked the good one
    assert [t["retry"] for t in ticks] == [1, 1, 0, 0] and ticks[2]["failed"] == 1 and ticks[3]["due"] == 0
    assert _status(tmp_path) == [("rău", "failed", 3), ("bun", "sent", 1)]


def test_tasks_survive_a_restart_and_a_half_sent_one_is_recovered(tmp_path):
    brain = _brain(tmp_path)
    asyncio.run(s.schedule(brain, 42, 100.0, "după restart"))
    asyncio.run(s.schedule(brain, 42, 100.0, "pe jumătate"))
    brain._db.execute("UPDATE scheduled_tasks SET status = 'sending', attempts = 1 WHERE text = 'pe jumătate'")
    brain._db.commit()
    brain.close()                                                         # the process dies

    brain, send = _brain(tmp_path), _Send()                               # a new process, same file
    try:
        assert [t["text"] for t in asyncio.run(s.pending(brain, 42))] == ["după restart", "pe jumătate"]
        assert asyncio.run(s.recover(brain)) == 1
        asyncio.run(s.run_due(brain, send, now=200.0))
    finally:
        brain.close()
    assert sorted(t for _, t in send.sent) == ["după restart", "pe jumătate"]


def test_an_empty_text_is_refused(tmp_path):
    brain = _brain(tmp_path)
    try:
        with pytest.raises(ValueError):
            asyncio.run(s.schedule(brain, 42, 100.0, "  "))
    finally:
        brain.close()


class _Working:
    async def get_messages(self, chat_id):
        return []


def test_the_loop_runs_on_tick_after_each_cycle_and_survives_its_failure(tmp_path):
    brain = Brain({**b._DEFAULTS, "enabled": True, "db_path": str(tmp_path / "brain.sqlite"), "interval_seconds": 0.01})
    ticks = []

    async def on_tick():
        ticks.append(1)
        if len(ticks) == 1:
            raise RuntimeError("first tick breaks")

    async def go():
        stop = asyncio.Event()
        task = asyncio.create_task(brain.run_forever(_Working(), ["42"], stop, on_tick))
        while len(ticks) < 3:
            await asyncio.sleep(0.01)
        stop.set()
        await task
    try:
        asyncio.run(asyncio.wait_for(go(), 5))
    finally:
        brain.close()
    assert len(ticks) >= 3                                                # a failing tick did not stop the loop


def test_end_to_end_the_bot_hook_sends_a_due_task_through_notify(monkeypatch, tmp_path):
    monkeypatch.setattr(b, "CONFIG", {**b._DEFAULTS, "enabled": True, "scheduler": True, "interval_seconds": 3600,
                                      "db_path": str(tmp_path / "brain.sqlite")})
    monkeypatch.setattr(b, "_instance", None)
    monkeypatch.setattr(b, "_sleep", None)
    sent = []

    async def fake_notify(chat_id, text, parse_mode=None):
        sent.append((chat_id, text))
    monkeypatch.setattr("telegram_interface.notify.notify", fake_notify)
    asyncio.run(s.schedule(b.get_instance(), "1912", 0.0, "Raportul e gata."))
    post_init, post_shutdown = _brain_sleep.post_init_hook(SimpleNamespace(working_memory=_Working()), "1912")

    async def go():
        await post_init(None)
        for _ in range(100):
            if sent:
                break
            await asyncio.sleep(0.01)
        await post_shutdown(None)
    try:
        asyncio.run(go())
    finally:
        b._instance.close()
    assert sent == [("1912", "Raportul e gata.")]
    assert _status(tmp_path) == [("Raportul e gata.", "sent", 1)]


def test_scheduler_off_means_no_tick(monkeypatch):
    monkeypatch.setattr(b, "CONFIG", {**b._DEFAULTS, "enabled": True, "scheduler": False})
    assert _brain_sleep._scheduler_tick() is None


class _L2:
    def __init__(self, fail=False):
        self.saved, self.fail = [], fail

    async def append_and_save_working_context(self, chat_id, *messages):
        if self.fail:
            raise RuntimeError("redis down")
        self.saved.extend((chat_id, m) for m in messages)


def _tick_with(monkeypatch, tmp_path, layers):
    monkeypatch.setattr(b, "CONFIG", {**b._DEFAULTS, "enabled": True, "scheduler": True,
                                      "db_path": str(tmp_path / "brain.sqlite")})
    monkeypatch.setattr(b, "_instance", None)
    sent = []

    async def fake_notify(chat_id, text, parse_mode=None):
        sent.append((chat_id, text))
    monkeypatch.setattr("telegram_interface.notify.notify", fake_notify)
    asyncio.run(s.schedule(b.get_instance(), "1912", 0.0, "⏰ 12:20 — măsurătoarea de +24h"))
    tick = _brain_sleep._scheduler_tick(layers)
    try:
        asyncio.run(tick())
        asyncio.run(tick())                                                # a second tick: nothing due any more
    finally:
        b._instance.close()
    return sent


def test_a_sent_scheduled_message_lands_in_the_history_as_goats_own(monkeypatch, tmp_path):
    l2 = _L2()
    sent = _tick_with(monkeypatch, tmp_path, l2)
    assert sent == [("1912", "⏰ 12:20 — măsurătoarea de +24h")]
    assert len(l2.saved) == 1
    chat_id, msg = l2.saved[0]
    assert chat_id == "1912" and msg["role"] == "assistant" and msg["source"] == "scheduled"
    assert msg["content"] == "⏰ 12:20 — măsurătoarea de +24h" and msg["timestamp"] > 0


def test_a_failed_history_write_never_sends_the_message_twice(monkeypatch, tmp_path):
    sent = _tick_with(monkeypatch, tmp_path, _L2(fail=True))
    assert len(sent) == 1                                                  # out once, history failure only logged
    assert _status(tmp_path) == [("⏰ 12:20 — măsurătoarea de +24h", "sent", 1)]
