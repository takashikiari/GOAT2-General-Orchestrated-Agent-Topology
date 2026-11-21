"""brain/encode.py: an understood turn becomes typed graph links, a meta row and open items. Nothing is deleted."""
from __future__ import annotations

import pathlib
import re

from brain import brain as b
from brain import encode as enc
from brain import graph as g
from brain.store import Brain

_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _brain(tmp_path):
    return Brain({**b._DEFAULTS, "enabled": True, "db_path": str(tmp_path / "brain.sqlite")})


def _u(**kw):
    u = {"type": "fapt", "importance": 0.6, "gist": "Laris a ales Prefetch", "about": ["Prefetch"], "links": [],
         "opens": None, "closes": []}
    u.update(kw)
    return u


def _n(db, table):
    return db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_about_reuses_existing_thing_node(tmp_path):
    brain = _brain(tmp_path)
    try:
        g.ingest(brain._db, [{"id": "m0", "ts": 1.0, "chat_id": "42", "entities": "Prefetch",
                              "entity_types": "technology"}], 1.0)
        before = brain._db.execute("SELECT count(*) FROM graph_nodes WHERE kind = 'thing'").fetchone()[0]
        r = enc.encode(brain._db, "42", "main", "e1", 10.0, _u(about=["Prefetch-ul", "ab"]), 10.0)
        assert brain._db.execute("SELECT count(*) FROM graph_nodes WHERE kind = 'thing'").fetchone()[0] == before
        assert brain._db.execute("SELECT 1 FROM graph_edges WHERE src='e1' AND dst='thing:prefetch' "
                                 "AND kind='despre'").fetchone()
        assert r["things"] == 1
    finally:
        brain.close()


def test_typed_links_are_edges_of_that_kind(tmp_path):
    brain = _brain(tmp_path)
    try:
        u = _u(links=[{"to": "e0", "kind": "cauză"}, {"to": "e00", "kind": "corectează"}])
        r = enc.encode(brain._db, "42", "main", "e1", 10.0, u, 10.0)
        rows = brain._db.execute("SELECT src, dst, kind, weight FROM graph_edges WHERE kind != 'despre' "
                                 "ORDER BY dst").fetchall()
        assert [(a, d, k) for a, d, k, _ in rows] == [("e1", "e0", "cauză"), ("e1", "e00", "corectează")]
        assert rows[0][3] == 0.5 and r["links"] == 2
        meta = brain._db.execute("SELECT type, gist FROM episode_meta WHERE id='e1'").fetchone()
        assert meta == ("fapt", "Laris a ales Prefetch")
    finally:
        brain.close()


def test_open_then_close_keeps_the_row(tmp_path):
    brain = _brain(tmp_path)
    try:
        r = enc.encode(brain._db, "42", "main", "e1", 10.0,
                       _u(opens={"kind": "promisiune", "text": "Voi verifica"}), 10.0)
        assert r["opened"] == 1
        r = enc.encode(brain._db, "42", "main", "e2", 20.0, _u(closes=["open:e1", "open:nope"]), 20.0)
        assert r["closed"] == 1
        q = "SELECT kind, text, closed_ts, closed_by FROM open_items WHERE id='open:e1'"
        assert brain._db.execute(q).fetchone() == ("promisiune", "Voi verifica", 20.0, "e2")
        enc.encode(brain._db, "42", "main", "e3", 30.0, _u(closes=["open:e1"]), 30.0)
        assert brain._db.execute(q).fetchone() == ("promisiune", "Voi verifica", 20.0, "e2")
    finally:
        brain.close()


def test_reencoding_the_same_turn_is_idempotent(tmp_path):
    brain = _brain(tmp_path)
    try:
        u = _u(links=[{"to": "e0", "kind": "urmare"}], opens={"kind": "întrebare", "text": "Cum?"})
        enc.encode(brain._db, "42", "main", "e1", 10.0, u, 10.0)
        c1 = g.counts(brain._db)
        n1 = [_n(brain._db, t) for t in ("episode_meta", "open_items")]
        enc.encode(brain._db, "42", "main", "e1", 10.0, u, 11.0)
        assert g.counts(brain._db) == c1
        assert [_n(brain._db, t) for t in ("episode_meta", "open_items")] == n1
    finally:
        brain.close()


def test_counts_never_decrease(tmp_path):
    brain = _brain(tmp_path)
    try:
        hist = []
        for i in range(4):
            u = _u(links=[{"to": f"e{i - 1}", "kind": "continuă"}], about=[f"Thing{i}x"])
            enc.encode(brain._db, "42", "main", f"e{i}", 10.0 + i, u, 10.0 + i)
            c = g.counts(brain._db)
            hist.append((c["total_nodes"], c["total_edges"]))
        assert all(a <= c for a, c in zip(hist, hist[1:])), hist
    finally:
        brain.close()


def test_no_delete_in_encode():
    src = (_ROOT / "brain/encode.py").read_text(encoding="utf-8")
    code = re.sub(r'""".*?"""', "", src, flags=re.S)
    assert not re.search(r"\bDELETE\b|\bDROP\b|\bTRUNCATE\b", code, re.I)
