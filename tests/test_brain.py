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


def test_paths_link_the_conversation_and_fade_without_deleting(tmp_path):
    brain, working = _brain(tmp_path, decay=0.5), _Working({"42": CHAT})
    asyncio.run(brain.sleep_once(working, ["42"]))
    kinds = sorted(r[0] for r in _rows(tmp_path, "SELECT kind FROM paths"))
    assert kinds == ["answered_by", "answered_by", "next_turn"]
    asyncio.run(brain.sleep_once(working, ["42"]))               # nothing walked → every link fades
    weights = [r[0] for r in _rows(tmp_path, "SELECT weight FROM paths")]
    assert len(weights) == 3 and all(0 < w < 1.0 for w in weights)   # weaker, still there


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


def test_small_slices_resume_next_cycle(tmp_path):
    many = [_m("user" if i % 2 == 0 else "assistant", f"mesaj {i}", float(i + 1)) for i in range(10)]
    brain = _brain(tmp_path, max_events_per_cycle=4)
    counts = [asyncio.run(brain.sleep_once(_Working({"42": many}), ["42"]))["events"] for _ in range(4)]
    assert counts == [4, 4, 2, 0]
    assert len(_rows(tmp_path, "SELECT id FROM cycles")) == 4       # every cycle is measured


def test_off_by_default_and_on_persistent_disk(tmp_path):
    assert b._DEFAULTS["enabled"] is False
    assert b.load_config(tmp_path / "missing.toml")["enabled"] is False
    assert b.load_config(Path(b._ROOT) / "config" / "brain.toml")["enabled"] is False
    assert b.start(_Working({}), ["42"], {**b._DEFAULTS, "enabled": False}) is None
    assert not b.load_config(tmp_path / "missing.toml")["db_path"].startswith("/tmp")
