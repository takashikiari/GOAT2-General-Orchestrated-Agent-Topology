"""Memory graph, phase F1 (brain/graph.py + brain/graph_ingest.py): built from L3, read by nothing yet.

Laris's absolute condition is tested first: there is no DELETE in the graph code, counts never decrease, a weight
never goes below the 0.001 floor. Then: cleaned things (no credentials, dates, times, generic words), ``despre`` and
``urmează`` links, idempotent re-ingest, the cursor that never moves back, and the slice fed from a fake Chroma.
"""
from __future__ import annotations

import asyncio
import pathlib
import re

from brain import brain as b
from brain import graph as g
from brain import graph_ingest as gi
from brain.store import Brain

_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _brain(tmp_path):
    return Brain({**b._DEFAULTS, "enabled": True, "db_path": str(tmp_path / "brain.sqlite")})


def _doc(i, ts, ents="", types="", topic=None, seq=None, chat="42"):
    return {"id": f"m{i}", "ts": ts, "chat_id": chat, "topic_id": topic, "sequence_number": seq,
            "entities": ents, "entity_types": types}


# --- the absolute condition ----------------------------------------------------------------------------------------
def test_there_is_no_delete_anywhere_in_the_graph_code():
    for name in ("brain/graph.py", "brain/graph_ingest.py"):
        src = (_ROOT / name).read_text(encoding="utf-8")
        code = "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith("#"))
        code = re.sub(r'""".*?"""', "", code, flags=re.S)
        assert not re.search(r"\bDELETE\b|\bDROP\b|\.unlink\(|\bTRUNCATE\b", code, re.I), name


def test_counts_never_decrease_and_weights_never_go_below_the_floor(tmp_path):
    brain = _brain(tmp_path)
    try:
        history = []
        for i in range(5):
            g.ingest(brain._db, [_doc(i, 100.0 + i, "Laris,Prefetch-ul", "person,technology", topic="t1", seq=i)], 1.0)
            g.ingest(brain._db, [_doc(i, 100.0 + i, "Laris", "person", topic="t1", seq=i)], 2.0)   # re-ingest
            c = g.counts(brain._db)
            history.append((c["total_nodes"], c["total_edges"]))
        assert all(a <= b_ for a, b_ in zip(history, history[1:])), history
        g._edge(brain._db, "m0", "thing:x", "despre", 3.0, weight=0.0)           # asked for 0: stored at the floor
        assert brain._db.execute("SELECT min(weight) FROM graph_edges").fetchone()[0] >= g.WEIGHT_FLOOR
    finally:
        brain.close()


# --- cleaning ------------------------------------------------------------------------------------------------------
def test_things_are_cleaned_and_credentials_never_become_nodes():
    assert g.norm_thing("Prefetch-ul", "technology") == g.norm_thing("prefetch", "technology") == "prefetch"
    assert g.norm_thing("ChromaDB", "technology") == "chromadb"
    assert g.norm_thing("Laris", "person") == "laris"
    for ent, typ in [("sk-abc123secret", "credential"), ("17:00", "time"), ("4 iulie 2026", "date"),
                     ("07:42", "technology"), ("Sistemul", "technology"), ("contorul", "technology"), ("ab", "person")]:
        assert g.norm_thing(ent, typ) is None, (ent, typ)


# --- links ---------------------------------------------------------------------------------------------------------
def test_despre_and_urmeaza_links_and_idempotent_reingest(tmp_path):
    brain = _brain(tmp_path)
    docs = [_doc(1, 100.0, "GLiNER,Laris", "technology,person", topic="t1", seq=1),
            _doc(2, 200.0, "gliner", "technology", topic="t1", seq=2),
            _doc(3, 300.0, "ChromaDB", "technology"),                     # no thread: same chat within 15 min
            _doc(4, 310.0, "", ""),
            _doc(5, 9000.0, "", "")]                                      # too late: no urmează from m4
    try:
        first = g.ingest(brain._db, docs, 1.0)
        again = g.ingest(brain._db, docs, 2.0)
        edges = sorted(brain._db.execute("SELECT src, dst, kind FROM graph_edges").fetchall())
    finally:
        brain.close()
    assert first["episodes"] == 5 and first["things"] == 3 and again == {"episodes": 0, "things": 0, "despre": 0,
                                                                          "urmeaza": 0}
    assert ("m1", "thing:gliner", "despre") in edges and ("m2", "thing:gliner", "despre") in edges   # same thing
    assert ("m1", "m2", "urmează") in edges and ("m3", "m4", "urmează") in edges
    assert not any(e[1] == "m5" and e[2] == "urmează" for e in edges)
    assert ("m2", "m3", "urmează") in edges          # no thread id: the same chat 100 s later is the conversation going on


def test_the_cursor_never_moves_back(tmp_path):
    brain = _brain(tmp_path)
    try:
        assert g.cursor(brain._db) == 0.0
        assert g.cursor(brain._db, value=500.0) == 500.0
        assert g.cursor(brain._db, value=100.0) == 500.0
    finally:
        brain.close()


# --- the tick slice, from a fake Chroma ----------------------------------------------------------------------------
class _Collection:
    def __init__(self, metas):
        self.metas, self.calls = metas, []

    def get(self, where=None, include=None):
        self.calls.append(where)
        lo = where["timestamp"]["$gte"]
        keep = [(f"c{i}", m) for i, m in enumerate(self.metas) if m["timestamp"] >= lo]
        return {"ids": [k for k, _ in keep], "metadatas": [m for _, m in keep]}


class _Episodic:
    def __init__(self, metas):
        self.col = _Collection(metas)

    def _get_collection(self):
        return self.col


def test_ingest_slice_backfills_in_slices_and_then_only_sees_new_memories(tmp_path):
    metas = [{"message_id": f"m{i}", "timestamp": 100.0 + i, "chat_id": "42", "entities": "Laris",
              "entity_types": "person"} for i in range(5)]
    brain, epi = _brain(tmp_path), _Episodic(metas)
    try:
        r1 = asyncio.run(gi.ingest_slice(brain, epi, limit=3))
        r2 = asyncio.run(gi.ingest_slice(brain, epi, limit=3))
        r3 = asyncio.run(gi.ingest_slice(brain, epi, limit=3))
        total = g.counts(brain._db)
    finally:
        brain.close()
    assert (r1["episodes"], r2["episodes"], r3.get("episodes", 0)) == (3, 2, 0)
    assert total["nodes"] == {"episode": 5, "thing": 1} and total["edges"]["despre"] == 5
    assert epi.col.calls[-1] == {"timestamp": {"$gte": 104.0}}         # the cursor moved to the last one ingested


def test_the_graph_tick_is_off_unless_configured(monkeypatch):
    from telegram_interface import _brain_sleep
    monkeypatch.setattr(b, "CONFIG", {**b._DEFAULTS, "enabled": True, "graph": False})
    assert _brain_sleep._graph_tick(object()) is None
    assert _brain_sleep._join(None, None) is None
