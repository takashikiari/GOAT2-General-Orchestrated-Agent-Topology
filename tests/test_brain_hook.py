"""The memory brain v0 hook in Orchestrator.run (2026-11-15): strictly gated by [brain] enabled, best-effort.

(a) enabled = false → the brain is never called (proven with a spy, not assumed);
(b) enabled = true  → the turn's messages land in the brain's store;
(c) the brain raises → GOAT still answers normally.
"""
from __future__ import annotations

import asyncio
import sqlite3

import pytest

from brain import brain as b
from orchestrator import orchestrator as orch_mod
from orchestrator.orchestrator import Orchestrator
from tests._orch_fakes import _Completions, _FakeAnalytics, _FakeLayers, _FakePluginManager, _LLMClient


@pytest.fixture
def turn(monkeypatch):
    async def fake_retrieve(layers, chat_id, query, state, activation, topic_return_id=None):
        return [], False, None, {}
    monkeypatch.setattr(orch_mod, "retrieve", fake_retrieve)

    def run(text="Cum stăm cu cache-ul?", **kw):
        layers = _FakeLayers()

        async def go():
            o = Orchestrator(layers, _LLMClient(_Completions("Verific și revin.")), _FakePluginManager(), _FakeAnalytics())
            reply = await o.run(text, "42", **kw)
            await o.drain_background()
            return reply
        return asyncio.run(go()), layers
    return run


@pytest.fixture
def brain_on(monkeypatch, tmp_path):
    cfg = {**b._DEFAULTS, "enabled": True, "db_path": str(tmp_path / "brain.sqlite")}
    monkeypatch.setattr(b, "CONFIG", cfg)
    monkeypatch.setattr(b, "_instance", None)
    yield tmp_path / "brain.sqlite"
    if b._instance is not None:
        b._instance.close()


def test_a_disabled_never_touches_the_brain(monkeypatch, turn, tmp_path):
    calls = []

    async def spy(*a, **kw):
        calls.append(a)
        return 0
    monkeypatch.setattr(b, "CONFIG", {**b._DEFAULTS, "enabled": False, "db_path": str(tmp_path / "x.sqlite")})
    monkeypatch.setattr(b, "record_turn", spy)
    monkeypatch.setattr(b, "Brain", lambda *a, **kw: calls.append("Brain()"))
    reply, layers = turn()
    assert reply == "Verific și revin."
    assert calls == []                                   # no record_turn, no store created
    assert not (tmp_path / "x.sqlite").exists()
    assert len(layers.saved) == 2                         # the normal L2 save still happened


def test_b_enabled_writes_the_turn_to_the_store(turn, brain_on):
    reply, layers = turn(lane="human")
    con = sqlite3.connect(brain_on)
    rows = con.execute("SELECT role, lane, text, l3_id FROM events ORDER BY ts, role DESC").fetchall()
    links = con.execute("SELECT kind FROM paths").fetchall()
    con.close()
    assert [r[0] for r in rows] == ["user", "assistant"]
    assert rows[0][1] == "human" and rows[0][2] == "Cum stăm cu cache-ul?" and rows[1][2] == reply
    assert rows[0][3] == layers.saved[0]["l3_id"]           # the same turn as the L2 messages
    assert links == [("answered_by",)]


def test_b_enabled_uses_the_turns_real_lane(turn, brain_on):
    turn(text="digest fără antet", lane="channel")
    con = sqlite3.connect(brain_on)
    assert {r[0] for r in con.execute("SELECT lane FROM events")} == {"channel"}
    con.close()


def test_c_brain_failure_never_costs_the_reply(monkeypatch, turn, brain_on):
    async def boom(*a, **kw):
        raise RuntimeError("store is on fire")
    monkeypatch.setattr(b, "record_turn", boom)
    reply, layers = turn()
    assert reply == "Verific și revin."
    assert len(layers.saved) == 2


def test_b_a_write_slower_than_the_slice_budget_is_still_stored(monkeypatch, turn, brain_on):
    # live 2026-11-15: the first write takes ~44 ms; cutting it at 20 ms stored nothing
    real = b.record_turn

    async def slow_but_fine(*a, **kw):
        await asyncio.sleep(0.06)
        return await real(*a, **kw)
    monkeypatch.setattr(b, "record_turn", slow_but_fine)
    turn()
    con = sqlite3.connect(brain_on)
    assert con.execute("SELECT count(*) FROM events").fetchone()[0] == 2
    con.close()


def test_c_slow_brain_never_delays_the_reply_and_still_stores(monkeypatch, brain_on, caplog):
    """A brain slower than record_slow_ms: the reply comes back first, the write is not cancelled (it lands), and the
    slowness is logged. Cancelling would release the store lock while the SQLite thread keeps writing."""
    import logging
    import time
    monkeypatch.setitem(b.CONFIG, "record_slow_ms", 100)
    real = b.record_turn
    finished = {}

    async def slow(*a, **kw):
        await asyncio.sleep(0.4)
        n = await real(*a, **kw)
        finished["at"] = time.perf_counter()
        return n
    monkeypatch.setattr(b, "record_turn", slow)

    async def fake_retrieve(layers, chat_id, query, state, activation, topic_return_id=None):
        return [], False, None, {}
    monkeypatch.setattr(orch_mod, "retrieve", fake_retrieve)

    async def go():
        o = Orchestrator(_FakeLayers(), _LLMClient(_Completions("Verific și revin.")), _FakePluginManager(), _FakeAnalytics())
        reply = await o.run("Cum stăm cu cache-ul?", "42")
        replied = time.perf_counter()
        await o.drain_background()
        return reply, replied
    caplog.set_level(logging.WARNING, logger="orchestrator.orchestrator")
    reply, replied = asyncio.run(go())
    assert reply == "Verific și revin."
    assert replied < finished["at"]                              # the reply did not wait for the brain
    con = sqlite3.connect(brain_on)
    assert con.execute("SELECT count(*) FROM events").fetchone()[0] == 2   # not cancelled: the turn is stored
    con.close()
    assert any("record_turn slow" in r.getMessage() for r in caplog.records)
