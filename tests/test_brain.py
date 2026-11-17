"""Memory brain v0 (brain/brain.py): blind write from L2 and the open-items nucleus (path links removed 2026-11-17)."""
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
    assert b.is_promise("Verific acum și revin cu cifrele.") and b.is_promise("O să verific logurile mâine.")
    assert not b.is_promise("Gata, am verificat.")
    assert not b.is_promise("O să verific logurile.")            # a promise needs a when (2026-11-15 tightening)


def test_promise_ignores_quotes_and_needs_verb_and_time_in_one_sentence():
    # a real promise
    assert b.is_promise("Rulez măsurătorile și revin cu cifrele la 16:20.")
    # the two live false positives (12:08, 12:21): GOAT quoting the rule's own words
    assert not b.is_promise("Nucleul are 0 itemi: regulile caută „revin/o să verific”, iar eu scriu la trecut.")
    assert not b.is_promise("Verb la persoana I sau viitor angajat („revin”, „trimit”) plus termen.")
    assert not b.is_promise('Am scris "o să verific mâine" ca exemplu de regulă.')
    # a real promise next to a quote still counts
    assert b.is_promise("Regula caută „revin”. Eu revin cu raportul mâine.")
    # verb and time in different sentences do not make a promise
    assert not b.is_promise("Revin la subiect. Mâine e duminică.")
    assert not b.is_promise("Am verificat. Revin. Ieri a mers.")


def test_mixed_quote_marks_are_still_a_quote():
    # live 2026-11-15 17:23, channel lane: a rule description quoted with „…" (ASCII close) became a "promise"
    live = '- `_NEGATED` — negație în cele 2 cuvinte dinaintea verbului; „nu pot acum, dar revin mâine" rămâne promisiune'
    assert not b.is_promise(live)
    assert not b.is_promise("Exemplu: “o să verific mâine\" e o regulă.")
    assert b.is_promise('Regula e „revin mâine". Eu revin cu cifrele la 12:20.')   # the real one next to it counts


def test_blind_write_reads_only_and_ingests_each_message_once(tmp_path):
    brain, working = _brain(tmp_path), _Working({"42": CHAT})
    first = asyncio.run(brain.sleep_once(working, ["42"]))
    again = asyncio.run(brain.sleep_once(working, ["42"]))     # nothing new → nothing written
    assert first["events"] == 4 and again["events"] == 0
    assert working.calls == ["42", "42"]
    assert [r[0] for r in _rows(tmp_path, "SELECT role FROM events ORDER BY ts")] == ["user", "assistant", "user", "assistant"]


def test_no_path_links_any_more_and_an_old_store_keeps_its_table(tmp_path):
    """2026-11-17 (Laris): the path links and their fading were removed (never reinforced, never read). A new store
    has no paths table; an old one keeps its table and rows untouched (no data is deleted)."""
    brain = _brain(tmp_path / "new")
    asyncio.run(brain.sleep_once(_Working({"42": CHAT}), ["42"]))
    brain.close()
    con = sqlite3.connect(tmp_path / "new" / "brain.sqlite")
    assert not con.execute("SELECT name FROM sqlite_master WHERE name = 'paths'").fetchall()
    con.close()
    con = sqlite3.connect(tmp_path / "brain.sqlite")
    con.execute("CREATE TABLE paths (src TEXT, dst TEXT, kind TEXT, weight REAL, last_reinforced REAL, faded_at REAL)")
    con.execute("INSERT INTO paths VALUES ('a', 'b', 'next_turn', 0.4, 0.0, 0.0)")
    con.commit()
    con.close()
    brain = _brain(tmp_path)
    asyncio.run(brain.sleep_once(_Working({"42": CHAT}), ["42"]))
    brain.close()
    assert _rows(tmp_path, "SELECT src, dst, kind, weight FROM paths") == [("a", "b", "next_turn", 0.4)]


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


def test_a_reply_to_a_digest_stays_in_the_channel_lane(tmp_path):
    chat = [_m("user", "Mesaje noi pe canalul agent-coord, de la x: „verifică logurile”", 10.0),
            _m("assistant", "Am verificat logurile, totul e ok.", 10.0),
            _m("user", "Salut GOAT", 20.0), _m("assistant", "Salut!", 20.0)]
    brain = _brain(tmp_path)
    asyncio.run(brain.sleep_once(_Working({"42": chat}), ["42"]))
    assert _rows(tmp_path, "SELECT role, lane FROM events ORDER BY ts, role DESC") == [
        ("user", "channel"), ("assistant", "channel"), ("user", "human"), ("assistant", "human")]


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
    events, _ = brain._ingest("42", CHAT, 0.0)            # budget already gone before the first row
    assert events >= 1                                          # at least one message per call, never zero


def test_the_request_hook_stores_the_whole_turn_even_on_a_slow_store(tmp_path, monkeypatch):
    monkeypatch.setattr(b, "CONFIG", {**b._DEFAULTS, "enabled": True, "db_path": str(tmp_path / "brain.sqlite")})
    monkeypatch.setattr(b, "_instance", None)
    turn = [_m("user", "Cum stăm?", 500.0), _m("assistant", "Bine.", 500.0)]
    assert asyncio.run(b.record_turn("42", turn, lane="human")) == 2   # both messages of the turn, not one
    b._instance.close()


def test_cycle_reports_the_whole_nucleus_next_to_the_refreshed_lanes(tmp_path, caplog):
    """2026-11-15: a cycle logged open_items=0 while the store still held 3 items (lanes not refreshed that cycle
    keep theirs). nucleus_total is what the whole nucleus holds after the cycle."""
    import logging
    brain = _brain(tmp_path)
    chat = CHAT + [_m("user", "Și ce facem cu wizard-ul?", 300.0)]
    first = asyncio.run(brain.sleep_once(_Working({"42": chat}), ["42"]))
    caplog.set_level(logging.INFO, logger="brain.store")
    again = asyncio.run(brain.sleep_once(_Working({"42": chat}), ["42"]))     # nothing new: no lane refreshed
    assert first["open_items"] == first["nucleus_total"] == 2
    assert again["open_items"] == 0 and again["nucleus_total"] == 2           # the items are still there
    assert any("open_items=0 nucleus_total=2" in r.getMessage() for r in caplog.records)
