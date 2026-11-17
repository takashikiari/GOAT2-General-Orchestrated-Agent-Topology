"""The living memory graph (design §12; brain/graph.py touch/fade/pairs, brain/recall.py, brain/live.py).

- a link is reinforced on every touch, fades with time down to the 0.001 floor, is never deleted;
- things met together get linked; hubs form no pairs;
- activation brings an associated memory, a faded link still conducts a strong stimulus;
- in a turn: recalled memories join the prompt first with why; after the reply the turn's links form and the walked
  links are touched — §12.6 "alive": touched links have a fresh last_used and a higher weight, untouched ones not;
- off: L3 results unchanged; an error never costs the turn.
"""
from __future__ import annotations

import asyncio
import pathlib
import re

import pytest

from brain import brain as b
from brain import graph as g
from brain import live
from brain import recall as rc
from brain.store import Brain

_ROOT = pathlib.Path(__file__).resolve().parents[1]
DAY = 86400.0


@pytest.fixture
def brain(tmp_path):
    br = Brain({**b._DEFAULTS, "enabled": True, "db_path": str(tmp_path / "brain.sqlite")})
    yield br
    br.close()


def _edge(br, a, b_, kind):
    if kind in g.UNDIRECTED and b_ < a:
        a, b_ = b_, a
    return br._db.execute("SELECT weight, uses, last_used, w_at FROM graph_edges WHERE src=? AND dst=? AND kind=?",
                          (a, b_, kind)).fetchone()


def test_no_delete_in_the_living_graph_code():
    for name in ("brain/graph.py", "brain/graph_ingest.py", "brain/recall.py", "brain/live.py"):
        code = re.sub(r'""".*?"""', "", (_ROOT / name).read_text(encoding="utf-8"), flags=re.S)
        code = "\n".join(ln for ln in code.splitlines() if not ln.strip().startswith("#"))
        assert not re.search(r"\bDELETE\b|\bDROP\b|\bTRUNCATE\b", code, re.I), name


def test_fading_halves_every_14_days_and_stops_at_the_floor():
    assert g.faded(1.0, 0.0, 14 * DAY) == pytest.approx(0.5)
    assert g.faded(1.0, 0.0, 28 * DAY) == pytest.approx(0.25)
    assert g.faded(1.0, 0.0, 10_000 * DAY) == g.WEIGHT_FLOOR
    assert g.faded(0.4, None, 99 * DAY) == 0.4                       # never brought up to date: as stored


def test_touch_creates_reinforces_from_the_faded_weight_and_counts_uses(brain):
    db = brain._db
    assert g.touch(db, "b", "a", "gândite_împreună", 0.2, 0.0) == pytest.approx(0.2)    # undirected: stored a-b
    w = g.touch(db, "a", "b", "gândite_împreună", 0.2, 14 * DAY)                         # 0.2 faded to 0.1, +0.2
    assert w == pytest.approx(0.3)
    weight, uses, last_used, w_at = _edge(brain, "a", "b", "gândite_împreună")
    assert (uses, last_used, w_at) == (2, 14 * DAY, 14 * DAY) and weight == pytest.approx(0.3)
    for _ in range(20):
        g.touch(db, "a", "b", "gândite_împreună", 0.2, 14 * DAY)
    assert _edge(brain, "a", "b", "gândite_împreună")[0] == 1.0                           # capped at 1


def test_fade_slice_updates_stale_links_only_and_never_lowers_the_counts(brain):
    db = brain._db
    g.touch(db, "x", "y", "împreună", 0.5, 0.0)
    g.touch(db, "y", "z", "împreună", 0.5, 30 * DAY)
    before = g.counts(db)["total_edges"]
    assert g.fade_slice(db, 30 * DAY + 3600) == 1                   # only the one untouched for over a day
    assert _edge(brain, "x", "y", "împreună")[0] == pytest.approx(0.5 * 0.5 ** ((30 * DAY + 3600) / DAY / 14))
    assert _edge(brain, "y", "z", "împreună")[0] == 0.5
    assert g.counts(db)["total_edges"] == before


def test_pairs_link_things_met_together_hubs_excluded_reinforced_on_new_meetings(brain):
    db = brain._db
    docs = [{"id": f"e{i}", "ts": 1000.0 + i, "chat_id": "42", "entities": "Laris,Eurotunnel,LeShuttle",
             "entity_types": "person,organization,organization"} for i in range(3)]
    docs += [{"id": f"o{i}", "ts": 2000.0 + i, "chat_id": "42", "entities": "Laris", "entity_types": "person"}
             for i in range(10)]
    g.ingest(db, docs, 5000.0)
    out = g.pairs_slice(db, limit=500, hub_fraction=0.5)            # laris is in 13/13 episodes: a hub
    assert out["episodes"] == 13
    pair = _edge(brain, "thing:eurotunnel", "thing:leshuttle", "împreună")
    assert pair is not None and pair[1] == 3                        # met in 3 episodes: touched 3 times
    assert _edge(brain, "thing:laris", "thing:eurotunnel", "împreună") is None
    assert g.pairs_slice(db, limit=500)["episodes"] == 0             # its cursor moved on


def test_activation_reaches_an_associated_memory_and_a_faded_link_still_conducts(brain):
    db = brain._db
    g.touch(db, "now", "assoc", "gândite_împreună", 1.0, 100 * DAY)     # strong, fresh
    g.touch(db, "now", "old", "gândite_împreună", 0.002, 0.0)           # faded to the floor long ago
    got = rc.spread(db, {"now": 1.0}, now=100 * DAY, limit=3)
    assert [c["id"] for c in got] == ["assoc", "old"]                    # the lived link wins, the faded one is there
    assert got[0]["activation"] > got[1]["activation"] > 0
    assert got[0]["path"] == [("assoc", "now", "gândite_împreună")]


class _Col:
    def __init__(self, docs):
        self.docs = docs

    def get(self, ids=None, include=None):
        keep = [i for i in ids if i in self.docs]
        return {"ids": keep, "documents": [self.docs[i] for i in keep], "metadatas": [{"message_id": i} for i in keep]}


class _Epi:
    def __init__(self, docs):
        self.col = _Col(docs)

    def _get_collection(self):
        return self.col


@pytest.fixture
def on(monkeypatch, tmp_path):
    monkeypatch.setattr(b, "CONFIG", {**b._DEFAULTS, "enabled": True, "graph_recall": True,
                                      "db_path": str(tmp_path / "live.sqlite")})
    monkeypatch.setattr(b, "_instance", None)
    yield b
    if b._instance is not None:
        b._instance.close()


def test_a_turn_recalls_by_association_then_forms_and_touches_links_alive(on):
    import time
    db, t0 = on.get_instance()._db, time.time() - 60          # links lived a minute ago
    g.touch(db, "seen", "assoc", "gândite_împreună", 0.6, t0)
    g.touch(db, "x", "untouched", "gândite_împreună", 0.6, t0)
    before_untouched = _edge(on.get_instance(), "x", "untouched", "gândite_împreună")
    epi = _Epi({"assoc": "Am stabilit că plecăm cu LeShuttle vineri dimineață din Folkestone."})
    l3 = [{"content": "ceva văzut", "blended_score": 0.7, "metadata": {"message_id": "seen"}, "mechanisms": ["bm25"]}]
    results, trace = asyncio.run(live.recall_into(epi, l3, []))
    assert results[0]["metadata"]["message_id"] == "assoc" and "amintit prin legătură" in results[0]["content"]
    assert "assoc" in trace
    reply = "Da, plecăm cu LeShuttle vineri dimineață din Folkestone, cum am stabilit."
    asyncio.run(live.after_turn("turn1", results, {"orig_after": ["assoc", "seen"]}, reply, trace))
    walked = _edge(on.get_instance(), "seen", "assoc", "gândite_împreună")
    assert walked[1] == 2 and walked[2] > t0 and walked[0] > 0.6         # §12.6: touched, fresher, stronger
    assert _edge(on.get_instance(), "turn1", "assoc", "gândite_împreună")[0] == pytest.approx(0.2)   # used: +0.2
    assert _edge(on.get_instance(), "turn1", "seen", "gândite_împreună")[0] == pytest.approx(0.05)   # only present
    assert _edge(on.get_instance(), "x", "untouched", "gândite_împreună") == before_untouched         # not touched


def test_off_or_broken_leaves_the_turn_as_without_the_graph(monkeypatch, tmp_path, on):
    l3 = [{"content": "a", "blended_score": 0.5, "metadata": {"message_id": "a"}}]
    monkeypatch.setattr(b, "CONFIG", {**b._DEFAULTS, "enabled": True, "graph_recall": False,
                                      "db_path": str(tmp_path / "off.sqlite")})
    assert asyncio.run(live.recall_into(None, l3, [])) == (l3, {})
    monkeypatch.setattr(b, "CONFIG", {**b._DEFAULTS, "enabled": True, "graph_recall": True,
                                      "db_path": str(tmp_path / "on.sqlite")})
    monkeypatch.setattr(b, "_instance", None)
    g.touch(b.get_instance()._db, "a", "z", "gândite_împreună", 1.0, 0.0)
    assert asyncio.run(live.recall_into(None, l3, [])) == (l3, {})   # no episodic store: the error is swallowed
    asyncio.run(live.after_turn("t", l3, None, "", {}))               # nothing to link: no error


def test_other_sessions_traffic_is_never_recalled_into_a_turn():
    assert live._not_lived("[2026-11-14T17:38:23+02:00] user: Mesaje noi pe canalul agent-coord, de la x")
    assert live._not_lived("user: [Workflow 'audit' completed successfully]")
    assert not live._not_lived("[2026-11-14T22:10:30+02:00] user: Ești genial. Mulțumesc pt discuția asta")


def test_recalled_memories_never_push_out_the_search_best_three(on):
    import time
    db, t0 = on.get_instance()._db, time.time() - 60
    g.touch(db, "s1", "assoc", "gândite_împreună", 1.0, t0)
    epi = _Epi({"assoc": "o amintire asociată, legată de s1"})
    l3 = [{"content": f"r{i}", "blended_score": s, "metadata": {"message_id": f"s{i}"}}
          for i, s in enumerate([0.9, 0.8, 0.7, 0.1], start=1)]
    results, _ = asyncio.run(live.recall_into(epi, l3, []))
    ranked = sorted(results, key=lambda r: r["blended_score"], reverse=True)      # what the assembler does
    assert [r["metadata"]["message_id"] for r in ranked[:3]] == ["s1", "s2", "s3"]
    assert ranked[3]["metadata"]["message_id"] == "assoc" and ranked[3]["blended_score"] == pytest.approx(0.693)
    assert live._after_top([]) == 0.5
    assert live._after_top([{"blended_score": 0.4}]) == pytest.approx(0.396)       # fewer than 3: under the weakest
