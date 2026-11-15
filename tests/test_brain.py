"""Memory brain v0 (brain/brain.py): blind write from L2, paths that strengthen and fade, the open-items nucleus."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

from brain import brain as b


class _Working:
    """Read-only fake of WorkingMemory: the brain may only call get_messages."""

    def __init__(self, chats):
        self.chats, self.calls = chats, []

    async def get_messages(self, chat_id):
        self.calls.append(chat_id)
        return list(self.chats.get(chat_id, []))

    def __getattr__(self, name):  # any write (save_messages, append…) would fail the test loudly
        raise AssertionError(f"brain must not call WorkingMemory.{name}")


def _brain(tmp_path, **over):
    return b.Brain({**b._DEFAULTS, "db_path": str(tmp_path / "brain.sqlite"), **over})


def _m(role, text, ts, **kw):
    return {"role": role, "content": text, "timestamp": ts, **kw}


CHAT = [_m("user", "Cum stăm cu cache-ul?", 100.0, l3_id="d1"),
        _m("assistant", "Verific acum și revin cu cifrele.", 101.0, l3_id="d1"),
        _m("user", "Ok, mersi", 200.0, l3_id="d2"),
        _m("assistant", "Cu plăcere.", 201.0, l3_id="d2")]


def _rows(tmp_path, sql):
    con = sqlite3.connect(tmp_path / "brain.sqlite")
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def test_rules():
    assert b.lane_of(_m("user", "Mesaje noi pe canalul agent-coord, de la…", 1)) == "channel"
    assert b.lane_of(_m("user", "[Workflow 'x' completed successfully]", 1)) == "dag"
    assert b.lane_of(_m("user", "salut", 1)) == "human"
    assert b.is_open_question("Cum stăm cu cache-ul?") and b.is_open_question("ce mai faci")
    assert not b.is_open_question("ai dreptate") and not b.is_open_question("Ok, mersi")
    assert b.is_promise("Verific acum și revin cu cifrele.") and b.is_promise("O să verific logurile.")
    assert not b.is_promise("Gata, am verificat.")


def test_blind_write_reads_only_and_ingests_each_message_once(tmp_path):
    brain, working = _brain(tmp_path), _Working({"42": CHAT})
    first = asyncio.run(brain.sleep_once(working, ["42"]))
    again = asyncio.run(brain.sleep_once(working, ["42"]))     # nothing new → nothing written
    assert first["events"] == 4 and again["events"] == 0
    assert working.calls == ["42", "42"]
    assert [r[0] for r in _rows(tmp_path, "SELECT role FROM events ORDER BY ts")] == ["user", "assistant", "user", "assistant"]


def test_paths_link_the_conversation(tmp_path):
    brain = _brain(tmp_path)
    asyncio.run(brain.sleep_once(_Working({"42": CHAT}), ["42"]))
    assert sorted(r[0] for r in _rows(tmp_path, "SELECT kind FROM paths")) == ["answered_by", "answered_by", "next_turn"]


def test_paths_fade_by_time_not_by_cycle(tmp_path):
    """Half-life 24 h on the time elapsed: two cycles 5 s apart leave the links practically intact, 24 h halve them,
    and nothing is ever deleted (GOAT's decision 2026-11-15: decay per cycle was wrong)."""
    now = [1_000_000.0]
    brain = b.Brain({**b._DEFAULTS, "db_path": str(tmp_path / "brain.sqlite")}, clock=lambda: now[0])
    working = _Working({"42": CHAT})
    asyncio.run(brain.sleep_once(working, ["42"]))                      # links created at weight 1.0
    for _ in range(2):
        now[0] += 5
        asyncio.run(brain.sleep_once(working, ["42"]))
    weights = [r[0] for r in _rows(tmp_path, "SELECT weight FROM paths")]
    assert len(weights) == 3 and all(w > 0.9999 for w in weights)       # 10 s of 24 h: invisible
    now[0] = 1_000_000.0 + 24 * 3600
    asyncio.run(brain.sleep_once(working, ["42"]))
    weights = [r[0] for r in _rows(tmp_path, "SELECT weight FROM paths")]
    assert len(weights) == 3 and all(abs(w - 0.5) < 1e-9 for w in weights)   # exactly half after 24 h
    now[0] += 24 * 3600 * 10                                             # ten days of silence
    asyncio.run(brain.sleep_once(working, ["42"]))
    weights = [r[0] for r in _rows(tmp_path, "SELECT weight FROM paths")]
    assert len(weights) == 3 and all(0 < w < 0.001 for w in weights)     # faint, never deleted


def test_a_store_made_before_time_fading_gets_the_column(tmp_path):
    con = sqlite3.connect(tmp_path / "brain.sqlite")
    con.execute("CREATE TABLE paths (src TEXT NOT NULL, dst TEXT NOT NULL, kind TEXT NOT NULL, weight REAL NOT NULL, "
                "last_reinforced REAL NOT NULL, PRIMARY KEY (src, dst, kind))")
    con.execute("INSERT INTO paths VALUES ('a', 'b', 'next_turn', 1.0, 0.0)")
    con.commit()
    con.close()
    brain = _brain(tmp_path)
    asyncio.run(brain.sleep_once(_Working({}), []))
    cols = {r[1] for r in _rows(tmp_path, "PRAGMA table_info(paths)")}
    assert "faded_at" in cols and _rows(tmp_path, "SELECT count(*) FROM paths") == [(1,)]


def test_nucleus_keeps_open_questions_and_promises(tmp_path):
    chat = CHAT + [_m("user", "Și ce facem cu wizard-ul?", 300.0)]
    brain = _brain(tmp_path)
    asyncio.run(brain.sleep_once(_Working({"42": chat}), ["42"]))
    items = asyncio.run(brain.nucleus("42"))
    assert [i["kind"] for i in items] == ["unanswered", "promised"]
    assert items[0]["text"] == "Și ce facem cu wizard-ul?"        # the question nobody answered yet
    assert "revin" in items[1]["text"]                            # what GOAT said it would do
    assert "Cum stăm cu cache-ul?" not in json.dumps(items, ensure_ascii=False)   # answered → not open


def test_lanes_stay_apart(tmp_path):
    chat = CHAT + [_m("user", "Mesaje noi pe canalul agent-coord, de la x: „ce faci?”", 150.0)]
    brain = _brain(tmp_path)
    asyncio.run(brain.sleep_once(_Working({"42": chat}), ["42"]))
    assert _rows(tmp_path, "SELECT lane, count(*) FROM events GROUP BY lane ORDER BY lane") == [("channel", 1), ("human", 4)]
    human = json.dumps(asyncio.run(brain.nucleus("42", "human")), ensure_ascii=False)
    assert "agent-coord" not in human                             # a digest never lands in Laris's open items
    assert asyncio.run(brain.nucleus("42", "channel")) == []      # a digest is not a short question


def test_a_reply_saved_with_the_same_timestamp_is_still_linked_to_its_question(tmp_path):
    same = [_m("user", "Ce faci?", 50.0), _m("assistant", "Bine, tu?", 50.0), _m("user", "Și eu", 60.0)]
    brain = _brain(tmp_path)
    asyncio.run(brain.sleep_once(_Working({"42": same}), ["42"]))
    assert sorted(r[0] for r in _rows(tmp_path, "SELECT kind FROM paths")) == ["answered_by", "next_turn"]


def test_a_reply_to_a_digest_stays_in_the_channel_lane(tmp_path):
    chat = [_m("user", "Mesaje noi pe canalul agent-coord, de la x: „verifică logurile”", 10.0),
            _m("assistant", "Am verificat logurile, totul e ok.", 10.0),
            _m("user", "Salut GOAT", 20.0), _m("assistant", "Salut!", 20.0)]
    brain = _brain(tmp_path)
    asyncio.run(brain.sleep_once(_Working({"42": chat}), ["42"]))
    assert _rows(tmp_path, "SELECT role, lane FROM events ORDER BY ts, role DESC") == [
        ("user", "channel"), ("assistant", "channel"), ("user", "human"), ("assistant", "human")]
    assert sorted(r[0] for r in _rows(tmp_path, "SELECT kind FROM paths")) == ["answered_by", "answered_by"]


def test_small_slices_resume_next_cycle(tmp_path):
    many = [_m("user" if i % 2 == 0 else "assistant", f"mesaj {i}", float(i + 1)) for i in range(10)]
    brain = _brain(tmp_path, max_events_per_cycle=4)
    counts = [asyncio.run(brain.sleep_once(_Working({"42": many}), ["42"]))["events"] for _ in range(4)]
    assert counts == [4, 4, 2, 0]
    assert len(_rows(tmp_path, "SELECT id FROM cycles")) == 4       # every cycle is measured


def test_the_test_suite_never_writes_the_real_store():
    # conftest.py: whatever config/brain.toml says, pytest runs with the brain off and on a test-only path
    assert b.CONFIG["enabled"] is False
    assert b.CONFIG["db_path"].startswith("/tmp/pytest-goat2/")


def test_env_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("GOAT_BRAIN_ENABLED", "1")
    monkeypatch.setenv("GOAT_BRAIN_DB", str(tmp_path / "x.sqlite"))
    cfg = b.load_config(tmp_path / "missing.toml")
    assert cfg["enabled"] is True and cfg["db_path"] == str(tmp_path / "x.sqlite")


def test_off_by_default_and_on_persistent_disk(tmp_path, monkeypatch):
    monkeypatch.delenv("GOAT_BRAIN_ENABLED", raising=False)
    monkeypatch.delenv("GOAT_BRAIN_DB", raising=False)
    assert b._DEFAULTS["enabled"] is False
    assert b.load_config(tmp_path / "missing.toml")["enabled"] is False
    # config/brain.toml itself is switched on per branch / per decision (enabled = true on feat/brain-v0 since
    # c594242, by Laris's approval); what must stay off is the code default and a missing file
    assert isinstance(b.load_config(Path(b._ROOT) / "config" / "brain.toml")["enabled"], bool)
    assert b.start(_Working({}), ["42"], {**b._DEFAULTS, "enabled": False}) is None
    assert not b.load_config(tmp_path / "missing.toml")["db_path"].startswith("/tmp")


def test_an_exhausted_budget_still_makes_progress(tmp_path):
    """Live 2026-11-15 11:38: in the busy bot process the first SQLite query alone took longer than the 20 ms
    slice, the loop broke before the first message and returned 0 events with no error, every time."""
    brain = _brain(tmp_path)
    events, links, _ = brain._ingest("42", CHAT, 0.0)            # budget already gone before the first row
    assert events >= 1                                          # at least one message per call, never zero


def test_the_request_hook_stores_the_whole_turn_even_on_a_slow_store(tmp_path, monkeypatch):
    monkeypatch.setattr(b, "CONFIG", {**b._DEFAULTS, "enabled": True, "db_path": str(tmp_path / "brain.sqlite")})
    monkeypatch.setattr(b, "_instance", None)
    turn = [_m("user", "Cum stăm?", 500.0), _m("assistant", "Bine.", 500.0)]
    assert asyncio.run(b.record_turn("42", turn, lane="human")) == 2   # both messages of the turn, not one
    b._instance.close()
