"""C1 (corrections-fixes): a correction supersedes the old memory at encode time; nothing is ever deleted."""
from __future__ import annotations

import sqlite3

import pytest

from brain import brain as b
from brain import encode as enc
from brain import mind, supersede
from brain.store import Brain


@pytest.fixture
def db(tmp_path):
    brain = Brain({**b._DEFAULTS, "enabled": True, "db_path": str(tmp_path / "brain.sqlite")})
    yield brain._db
    brain.close()


def _u(**kw):
    u = {"type": "fapt", "importance": 0.6, "gist": "", "about": [], "links": [], "opens": None, "closes": []}
    u.update(kw)
    return u


def _meta(db, eid):
    return db.execute("SELECT gist, superseded_by, superseded_at FROM episode_meta WHERE id = ?", (eid,)).fetchone()


def test_migrate_adds_supersede_columns_to_an_old_store(tmp_path):
    old = sqlite3.connect(tmp_path / "old.sqlite")
    old.execute("CREATE TABLE episode_meta (id TEXT PRIMARY KEY, type TEXT, importance REAL, gist TEXT, "
                "encoded_at REAL)")
    old.execute("CREATE TABLE fact_kind (key TEXT PRIMARY KEY, kind TEXT NOT NULL, classified_at REAL)")
    old.execute("INSERT INTO episode_meta VALUES ('x', 'fapt', 0.5, 'vechi', 1.0)")
    mind.migrate(old)
    mind.migrate(old)  # idempotent
    assert {"superseded_by", "superseded_at"} <= {r[1] for r in old.execute("PRAGMA table_info(episode_meta)")}
    assert old.execute("SELECT gist, superseded_by FROM episode_meta").fetchone() == ("vechi", None)
    assert old.execute("SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = 'episode_meta_superseded_by'"
                       ).fetchone()


def test_fresh_store_has_the_superseded_by_index(db):
    plan = " ".join(r[3] for r in db.execute("EXPLAIN QUERY PLAN SELECT id FROM episode_meta WHERE superseded_by = ?",
                                             ("e",)))
    assert "episode_meta_superseded_by" in plan


def test_correction_supersedes_the_old_episode_and_drops_it_from_mind(db):
    enc.encode(db, "42", "human", "x", 10.0, _u(gist="Kent costă 120."), 10.0)
    for lane in ("human", "agent"):
        mind.light(db, "42", lane, "x", "amintire", 0.9, "căutare", "seamănă", 11.0)
    mind.light(db, "43", "human", "x", "amintire", 0.9, "căutare", "seamănă", 11.0)
    enc.encode(db, "42", "human", "e", 20.0, _u(type="corectură", gist="Kent costă 140.",
                                                 links=[{"to": "x", "kind": "corectează"}]), 21.0)
    assert _meta(db, "x") == ("Kent costă 120.", "e", 21.0)  # the old gist is history, unchanged
    acts = {(r[0], r[1]): r[2] for r in db.execute("SELECT chat_id, lane, activation FROM mind WHERE item='x'")}
    assert acts[("42", "human")] == acts[("42", "agent")] == b.MIND["floor"]
    assert acts[("43", "human")] == 0.9                       # another chat's mind is not touched
    assert supersede.head(db, "x") == "e" and supersede.head(db, "e") == "e"
    assert supersede.superseded(db, ["x", "e", "nope"]) == {"x"}


def test_only_the_correcting_kinds_supersede(db):
    for eid in ("a", "c"):
        enc.encode(db, "42", "human", eid, 10.0, _u(gist=eid), 10.0)
    enc.encode(db, "42", "human", "e", 20.0, _u(type="corectură", links=[
        {"to": "a", "kind": "continuă"}, {"to": "c", "kind": "contrazice"}]), 20.0)
    assert _meta(db, "a")[1] is None and _meta(db, "c")[1] == "e"


def test_an_episode_without_meta_gets_a_meta_row_with_only_the_supersede_fields(db):
    enc.encode(db, "42", "human", "e", 20.0, _u(links=[{"to": "old", "kind": "corectează"}]), 21.0)
    row = db.execute("SELECT type, gist, encoded_at, superseded_by FROM episode_meta WHERE id='old'").fetchone()
    assert row == (None, None, None, "e")
    enc.encode(db, "42", "human", "old", 5.0, _u(gist="backfill"), 30.0)   # a later backfill keeps the mark
    assert _meta(db, "old")[:2] == ("backfill", "e")


def test_chain_and_no_cycles(db):
    enc.encode(db, "42", "human", "x", 10.0, _u(gist="v1"), 10.0)
    enc.encode(db, "42", "human", "e1", 20.0, _u(gist="v2", links=[{"to": "x", "kind": "corectează"}]), 20.0)
    enc.encode(db, "42", "human", "e2", 30.0, _u(gist="v3", links=[{"to": "e1", "kind": "corectează"}]), 30.0)
    assert supersede.head(db, "x") == "e2" and supersede.head(db, "e1") == "e2"
    # an older turn re-encoded as "correcting" a newer one never makes a cycle
    enc.encode(db, "42", "human", "x", 10.0, _u(gist="v1", links=[{"to": "e2", "kind": "contrazice"}]), 40.0)
    assert _meta(db, "e2")[1] is None and supersede.head(db, "x") == "e2"
    # correcting a superseded memory corrects its current version: the chain grows, nothing is rewired
    enc.encode(db, "42", "human", "e3", 50.0, _u(gist="v4", links=[{"to": "x", "kind": "corectează"}]), 50.0)
    assert _meta(db, "x")[1] == "e1" and _meta(db, "e2")[1] == "e3" and supersede.head(db, "x") == "e3"
    assert [h[1] for h in supersede.history(db, "e3")] == ["v1", "v2", "v3", "v4"]
    assert [h[0] for h in supersede.history(db, "x")] == ["x", "e1", "e2", "e3"]


def test_head_survives_a_cycle_written_by_hand(db):
    for eid, ts in (("p", 1.0), ("q", 2.0)):
        enc.encode(db, "42", "human", eid, ts, _u(gist=eid), ts)
    db.execute("UPDATE episode_meta SET superseded_by = 'q' WHERE id = 'p'")
    db.execute("UPDATE episode_meta SET superseded_by = 'p' WHERE id = 'q'")
    assert supersede.head(db, "p") in {"p", "q"}                 # terminates


def test_supersede_is_part_of_the_encode_transaction(db, monkeypatch):
    enc.encode(db, "42", "human", "x", 10.0, _u(gist="v1", opens={"kind": "promisiune", "text": "alt"}), 10.0)

    def boom(*a, **k):
        raise RuntimeError("disk")
    monkeypatch.setattr(enc, "same_open", boom)
    with pytest.raises(RuntimeError):
        enc.encode(db, "42", "human", "e", 20.0, _u(links=[{"to": "x", "kind": "corectează"}],
                                                     opens={"kind": "promisiune", "text": "t"}), 20.0)
    db.commit()
    assert _meta(db, "x")[1] is None                          # rolled back with the rest of the episode


def test_turn_prompt_asks_to_link_corrections_to_the_memory_and_state_the_corrected_version():
    from brain.understand import TURN_PROMPT
    p = " ".join(TURN_PROMPT.split())
    assert "corectează sau contrazice ceva din „În minte”" in p
    assert "id-ul acelei amintiri" in p and "versiunea corectată" in p


def test_encode_returns_the_ids_it_superseded(db):
    enc.encode(db, "42", "human", "x", 10.0, _u(gist="v1"), 10.0)
    enc.encode(db, "42", "human", "e1", 20.0, _u(gist="v2", links=[{"to": "x", "kind": "corectează"}]), 20.0)
    r = enc.encode(db, "42", "human", "e2", 30.0, _u(gist="v3", links=[{"to": "x", "kind": "contrazice"},
                                                                     {"to": "e1", "kind": "continuă"}]), 30.0)
    assert r["superseded"] == ["e1"]                          # x's current version is what got superseded
    assert enc.encode(db, "42", "human", "n", 40.0, _u(gist="n"), 40.0)["superseded"] == []


def test_after_turn_and_backfill_log_the_superseded_ids(db, monkeypatch, caplog):
    import asyncio
    import logging
    import time
    from types import SimpleNamespace

    from brain import mind_live, mind_sleep

    monkeypatch.setattr(b, "MIND", {**b._MIND_DEFAULTS, "enabled": True})
    monkeypatch.setattr(b, "get_instance", lambda: SimpleNamespace(_db=db, lock=asyncio.Lock()))
    enc.encode(db, "42", "human", "x", time.time() - 60, _u(gist="v1"), time.time() - 60)

    async def understood(*a, **k):
        return _u(gist="v2", links=[{"to": "x", "kind": "corectează"}])
    monkeypatch.setattr(mind_live, "understand_turn", understood)
    caplog.set_level(logging.INFO, logger="brain.mind_live")
    asyncio.run(mind_live.after_turn(None, "42", "human", "t1", "nu", "ok", [], {}))
    assert any("'superseded': ['x']" in r.getMessage() for r in caplog.records)
    src = open(mind_sleep.__file__, encoding="utf-8").read()
    assert "superseded" in src and 'log.info("brain: backfill %s superseded %s"' in src


def test_restore_undoes_a_wrong_supersede_without_deleting(db):
    enc.encode(db, "42", "human", "x", 10.0, _u(gist="v1"), 10.0)
    enc.encode(db, "42", "human", "e", 20.0, _u(gist="părere", links=[{"to": "x", "kind": "contrazice"}]), 20.0)
    edges = db.execute("SELECT count(*) FROM graph_edges").fetchone()[0]
    assert supersede.head(db, "x") == "e"
    assert supersede.restore(db, "x") is True
    assert _meta(db, "x") == ("v1", None, None) and supersede.head(db, "x") == "x"
    assert db.execute("SELECT count(*) FROM graph_edges").fetchone()[0] == edges   # the edge stays
    assert supersede.restore(db, "x") is False and supersede.restore(db, "nope") is False
