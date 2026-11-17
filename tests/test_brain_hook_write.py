"""The request hook's write (brain.record_turn → Brain._hook_write, 2026-11-16):

- fix 1: the nucleus of the lane the turn wrote is refreshed right away, not at the next sleep cycle;
- fix 2: the hook's events are counted for the next cycle as cycles.hook_events (cycles.events keeps its meaning:
  what the cycle itself ingested), once, and the count survives a restart; an older store gets the column;
- the cycle log shows both numbers.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3

import pytest

from brain import brain as b
from brain.store import Brain


class _Working:
    def __init__(self, chats=None):
        self.chats = chats or {}

    async def get_messages(self, chat_id):
        return list(self.chats.get(chat_id, []))


_TURN = [{"role": "user", "content": "Când vine raportul?", "timestamp": 100.0, "l3_id": "d1"},
         {"role": "assistant", "content": "Rulez măsurătoarea și revin cu cifrele la 12:20.", "timestamp": 101.0,
          "l3_id": "d1"}]


@pytest.fixture
def brain_on(monkeypatch, tmp_path):
    monkeypatch.setattr(b, "CONFIG", {**b._DEFAULTS, "enabled": True, "db_path": str(tmp_path / "brain.sqlite")})
    monkeypatch.setattr(b, "_instance", None)
    yield tmp_path / "brain.sqlite"
    if b._instance is not None:
        b._instance.close()


def _cycles(path):
    con = sqlite3.connect(path)
    try:
        return con.execute("SELECT events, hook_events FROM cycles ORDER BY id").fetchall()
    finally:
        con.close()


def test_fix1_the_nucleus_is_fresh_right_after_the_hook_no_cycle_needed(brain_on):
    assert asyncio.run(b.record_turn("42", _TURN, lane="human")) == 2
    items = asyncio.run(b.nucleus("42", "human"))                     # no sleep cycle has run
    assert [(i["kind"], i["text"]) for i in items] == [("promised", "Rulez măsurătoarea și revin cu cifrele la 12:20.")]
    assert _cycles(brain_on) == []


def test_fix2_hook_events_are_reported_once_by_the_next_cycle(brain_on, caplog):
    asyncio.run(b.record_turn("42", _TURN, lane="human"))
    brain = b.get_instance()
    caplog.set_level(logging.INFO, logger="brain.store")
    first = asyncio.run(brain.sleep_once(_Working({"42": _TURN}), ["42"]))   # L2 has the same turn: already stored
    second = asyncio.run(brain.sleep_once(_Working({"42": _TURN}), ["42"]))
    assert (first["events"], first["hook_events"]) == (0, 2)                  # events keeps its meaning (cycle's own)
    assert (second["events"], second["hook_events"]) == (0, 0)                # counted once
    assert _cycles(brain_on) == [(0, 2), (0, 0)]
    assert any("events=0 hook_events=2 open_items=" in r.getMessage() for r in caplog.records)


def test_fix2_the_pending_count_survives_a_restart(brain_on):
    asyncio.run(b.record_turn("42", _TURN, lane="human"))
    b._instance.close()
    b._instance = None                                                        # the process dies before any cycle
    brain = b.get_instance()
    result = asyncio.run(brain.sleep_once(_Working(), ["42"]))
    assert result["hook_events"] == 2


def test_fix2_an_older_store_gets_the_column_and_keeps_its_rows(tmp_path):
    path = tmp_path / "brain.sqlite"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE cycles (id INTEGER PRIMARY KEY AUTOINCREMENT, started REAL NOT NULL, ms REAL NOT NULL, "
                "events INTEGER NOT NULL, links INTEGER NOT NULL, open_items INTEGER NOT NULL)")
    con.execute("INSERT INTO cycles (started, ms, events, links, open_items) VALUES (1, 5, 3, 2, 1)")
    con.commit()
    con.close()
    brain = Brain({**b._DEFAULTS, "enabled": True, "db_path": str(path)})
    brain.close()
    assert _cycles(path) == [(3, 0)]                                          # the baseline row intact, hook_events 0
