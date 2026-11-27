"""C2 + C3 (corrections-fixes): exposure never shows a superseded memory as current — not from a fresh search, not
from the warm prefetch cache (activation.merged), not through the graph — and the newest version's line says what it
replaced (reconsolidation, spec F5). Nothing is ever deleted."""
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from brain import brain as b
from brain import encode as enc
from brain import mind, recall, supersede
from brain import mind_live as ml
from memory.activation import rescore_recency

DAY = 86400.0


class FakeClient:
    def __init__(self, payload):
        self._payload = payload
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kw):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(self._payload)),
                                                        finish_reason="stop")])


class FakeEpisodic:
    def __init__(self, docs):
        self.docs = docs

    def _get_collection(self):
        return self

    def get(self, ids=None, include=None):
        keep = [i for i in ids if i in self.docs]
        return {"ids": keep, "documents": [self.docs[i][0] for i in keep],
                "metadatas": [{"message_id": i, "timestamp": self.docs[i][1]} for i in keep]}


@pytest.fixture
def on(monkeypatch, tmp_path):
    monkeypatch.setattr(b, "CONFIG", {**b._DEFAULTS, "enabled": True, "db_path": str(tmp_path / "mind.sqlite")})
    monkeypatch.setattr(b, "MIND", {**b._MIND_DEFAULTS, "enabled": True})
    monkeypatch.setattr(b, "_instance", None)
    yield b.get_instance()
    if b._instance is not None:
        b._instance.close()


NOW = time.time()
OLD, NEW, NEWER = "Prețul pentru Kent este 120 de lire.", "Laris a corectat: prețul Kent e 140.", "Kent e 150 acum."
DOCS = {"x": ("user: Kent costă 120", NOW - 3 * DAY), "e": ("user: nu, 140", NOW - DAY),
        "e2": ("user: de fapt 150", NOW - 3600)}
QUIET = {"about": [], "asks_past": False}


def _encode(brain, eid, ts, **kw):
    u = {"type": "fapt", "importance": 0.5, "gist": "", "about": [], "links": [], "opens": None, "closes": []}
    u.update(kw)
    enc.encode(brain._db, "42", "human", eid, ts, u, ts)


def _corrected(brain, chain=False):
    _encode(brain, "x", NOW - 3 * DAY, gist=OLD, about=["Kent"])
    _encode(brain, "e", NOW - DAY, type="corectură", gist=NEW, links=[{"to": "x", "kind": "corectează"}])
    if chain:
        _encode(brain, "e2", NOW - 3600, type="corectură", gist=NEWER, links=[{"to": "e", "kind": "contrazice"}])


def _hit(mid, score, text, ts):
    return {"id": mid, "content": text, "blended_score": score, "metadata": {"message_id": mid, "timestamp": ts}}


def _expose(message, hits, cues=QUIET):
    return asyncio.run(ml.expose(FakeClient(cues), FakeEpisodic(DOCS), "42", "human", message, hits, {}, 4000))


def _counts(db):
    return {t: db.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            for t in ("graph_nodes", "graph_edges", "episode_meta", "mind")}


def test_fresh_search_returning_the_old_memory_shows_the_correction(on):
    _corrected(on)
    block, shown, texts = _expose("cât costă Kent?", [_hit("x", 0.8, "user: Kent costă 120", NOW - 3 * DAY)])
    assert "x" not in shown and "e" in shown
    assert OLD not in block.split("(înlocuiește:")[0] and NEW in block
    assert "corectat ulterior" in block
    row = {r["item"]: r for r in mind.top(on._db, "42", "human", 10, 0.0)}
    assert "corectură" in row["e"]["sources"] and row["e"]["activation"] == pytest.approx(0.8)


def test_warm_turn_from_the_prefetch_cache_shows_the_correction(on):
    _corrected(on)
    merged = [{"id": "x", "content": "user: Kent costă 120", "score": 0.9, "blended_score": 0.9,
               "metadata": {"message_id": "x", "timestamp": NOW - 3 * DAY}}]
    hits = rescore_recency(merged, time.time())                  # what a warm turn serves, with no search
    block, shown, _ = _expose("și?", hits)
    assert shown == ["e"] and NEW in block and "Kent costă 120" not in block


def test_spread_from_the_correction_does_not_light_the_old_memory(on):
    _corrected(on)
    got = recall.spread(on._db, {"e": 1.0}, NOW, 10, 3, 14.0)
    assert "x" not in {c["id"] for c in got}
    back = recall.spread(on._db, {"x": 1.0}, NOW, 10, 3, 14.0)   # the old one still leads to the new one
    assert "e" in {c["id"] for c in back}


def test_the_graph_reaching_the_old_memory_lights_its_newest_version(on):
    _corrected(on)
    block, shown, _ = _expose("Kent?", [], {"about": ["Kent"], "asks_past": False})
    assert "x" not in shown and "e" in shown and NEW in block
    assert mind.top(on._db, "42", "human", 10, 0.0)[0]["item"] == "e"


def test_a_superseded_memory_already_in_mind_never_appears(on):
    _encode(on, "x", NOW - 3 * DAY, gist=OLD)
    mind.light(on._db, "42", "human", "x", "amintire", 1.0, "căutare", "seamănă", NOW)
    _encode(on, "e", NOW - DAY, type="corectură", gist=NEW, links=[{"to": "x", "kind": "corectează"}])
    on._db.execute("UPDATE mind SET activation = 1.0 WHERE item = 'x'")   # belt and braces: even if lit again
    on._db.commit()
    block, shown, _ = _expose("ok", [])
    assert "x" not in shown and OLD not in block.split("(înlocuiește:")[0]


def test_a_chain_shows_only_the_newest(on):
    _corrected(on, chain=True)
    hits = [_hit("x", 0.7, "user: Kent costă 120", NOW - 3 * DAY), _hit("e", 0.6, "user: nu, 140", NOW - DAY)]
    block, shown, _ = _expose("Kent?", hits, {"about": ["Kent"], "asks_past": False})
    assert shown == ["e2"] and NEWER in block
    assert supersede.head(on._db, "x") == "e2"


def test_the_new_line_says_what_it_replaced_and_the_old_gist_is_unchanged(on):
    _corrected(on)
    block, _, _ = _expose("Kent?", [_hit("x", 0.8, "user: Kent costă 120", NOW - 3 * DAY)])
    line = next(ln for ln in block.splitlines() if NEW in ln)
    day = time.strftime("%Y-%m-%d", time.localtime(NOW - 3 * DAY))
    assert f"(înlocuiește: {OLD[:80]}, {day})" in line
    assert on._db.execute("SELECT gist FROM episode_meta WHERE id = 'x'").fetchone()[0] == OLD
    assert [h[0] for h in supersede.history(on._db, "e")] == ["x", "e"]


def test_nothing_is_deleted(on):
    _corrected(on, chain=True)
    before = _counts(on._db)
    _expose("Kent?", [_hit("x", 0.8, "user: Kent costă 120", NOW - 3 * DAY)], {"about": ["Kent"], "asks_past": True})
    after = _counts(on._db)
    assert all(after[t] >= before[t] for t in before)
    assert on._db.execute("SELECT count(*) FROM graph_edges WHERE kind IN ('corectează', 'contrazice')"
                          ).fetchone()[0] == 2                    # the correcting edges stay stored


def test_ordinary_memories_never_say_they_replace_something(on):
    long_text = "user: " + "un drum lung spre Dover cu multe opriri " * 8           # > 160 chars, no gist
    _encode(on, "g", NOW - 2 * DAY, gist="Laris a ales feribotul.")
    docs = {**DOCS, "long": (long_text, NOW - DAY),
            "g": ("Laris a ales feribotul. Apoi a cerut și prețul pentru Dover.", NOW - 2 * DAY)}
    _corrected(on)
    hits = [_hit("long", 0.9, long_text, NOW - DAY), _hit("x", 0.8, "user: Kent costă 120", NOW - 3 * DAY),
            _hit("g", 0.7, docs["g"][0], NOW - 2 * DAY)]
    block, shown, _ = asyncio.run(ml.expose(FakeClient(QUIET), FakeEpisodic(docs), "42", "human", "x", hits, {},
                                            4000))
    assert {"long", "g", "e"} <= set(shown)
    lines = {mid: next(ln for ln in block.splitlines() if ln.startswith("- (") and key in ln)
             for mid, key in (("long", "un drum lung"), ("g", "Laris a ales feribotul"), ("e", NEW))}
    assert "înlocuiește" not in lines["long"] and "înlocuiește" not in lines["g"]
    assert f"(înlocuiește: {OLD}" in lines["e"] and block.count("înlocuiește") == 1


def test_a_head_that_is_also_a_direct_hit_keeps_the_hit_why(on):
    _corrected(on)
    for e_score, x_score in ((0.5, 0.8), (0.8, 0.5)):           # hits are lit weakest first: both orders
        on._db.execute("UPDATE mind SET activation = 0.0, why = '', sources = '[]'")
        on._db.commit()
        _expose("Kent?", [_hit("e", e_score, "user: nu, 140", NOW - DAY),
                          _hit("x", x_score, "user: Kent costă 120", NOW - 3 * DAY)])
        row = {r["item"]: r for r in mind.top(on._db, "42", "human", 10, 0.0)}["e"]
        assert row["why"] == "seamănă cu ce spui" and set(row["sources"]) == {"căutare", "corectură"}
