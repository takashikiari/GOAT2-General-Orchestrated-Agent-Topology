"""The prefetch feedback, phase 1 (brain/boost.py + memory/retrieval.py, 2026-11-15).

- the bonus is weight * min(1, uses/cap), never above 0.05 even if the config asks more; blended_score capped at 1;
- ce_score keeps the CrossEncoder score, score_boost the bonus; the input is never modified;
- wave-2 neighbours are never boosted and stay after the seeds; no uses → nothing changes;
- off ([brain] enabled or prefetch_boost false) → retrieve() output is exactly what it was; a broken brain → untouched;
- on, through retrieve(): a doc used before overtakes a close neighbour, a far one does not.
"""
from __future__ import annotations

import asyncio

import pytest

from brain import brain as b
from brain import boost as bo
from brain import usage
from memory.retrieval import retrieve


def _r(mid, score, mech="semantic_global", **kw):
    return {"content": mid, "metadata": {"message_id": mid}, "blended_score": score, "mechanisms": [mech], **kw}


def test_bonus_is_bounded_and_scores_stay_observable():
    res = [_r("a", 0.60), _r("b", 0.58), _r("c", 0.99)]
    ranked, stats = bo.apply(res, {"b": 3.0, "c": 30.0}, weight=0.5, cap=3)      # the config asks 0.5: clamped
    by = {r["metadata"]["message_id"]: r for r in ranked}
    assert by["b"]["score_boost"] == pytest.approx(0.05) and by["b"]["ce_score"] == 0.58
    assert by["b"]["blended_score"] == pytest.approx(0.63)
    assert by["c"]["blended_score"] == 1.0                                         # capped at 1
    assert "score_boost" not in by["a"] and by["a"]["blended_score"] == 0.60       # never used: untouched
    assert [r["metadata"]["message_id"] for r in ranked] == ["c", "b", "a"]
    assert stats == {"boosted": 2, "moved": 2, "max_delta": 0.05, "top3_changed": 0}
    assert res[1]["blended_score"] == 0.58 and "ce_score" not in res[1]            # input not modified


def test_partial_uses_give_a_partial_bonus_and_far_docs_do_not_jump():
    ranked, stats = bo.apply([_r("a", 0.90), _r("b", 0.50)], {"b": 1.5}, weight=0.05, cap=3)
    assert ranked[1]["score_boost"] == pytest.approx(0.025)                       # half of cap → half of the bonus
    assert [r["metadata"]["message_id"] for r in ranked] == ["a", "b"] and stats["moved"] == 0


def test_wave2_neighbours_are_never_boosted_and_stay_last():
    res = [_r("a", 0.6), _r("n", 0.1, mech="wave2", wave2_of="a"), _r("b", 0.59)]
    ranked, _ = bo.apply(res, {"n": 3.0, "b": 3.0}, weight=0.05, cap=3)
    assert [r["metadata"]["message_id"] for r in ranked] == ["b", "a", "n"]
    assert "score_boost" not in ranked[2]


class _Layers:
    """cold path; the 'rerank' gives a 0.60, b 0.58, c 0.30 (CrossEncoder-like scores)."""
    _ce = {"a": 0.60, "b": 0.58, "c": 0.30}

    async def search_episodic_with_cache(self, chat_id, query, limit=5, chat_id_filter=None):
        return ([_r("a", 0), _r("b", 0), _r("c", 0)] if chat_id_filter is None else []), False, "k"

    async def bm25_search(self, query, limit=15):
        return []

    async def extract_query_entities(self, query):
        return {"entities": [], "entity_types": []}

    async def boost_by_entities(self, query, results, pre_extracted=None):
        return results

    async def rerank(self, query, results):
        out = [{**r, "blended_score": self._ce[r["metadata"]["message_id"]]} for r in results]
        return sorted(out, key=lambda r: r["blended_score"], reverse=True)

    async def bump_access(self, chat_id, ids):
        pass


@pytest.fixture
def cold(monkeypatch):
    import memory.config as _cfg
    monkeypatch.setattr(_cfg, "L3_MIN_ALNUM", 0)

    def run():
        merged, _, _, _ = asyncio.run(retrieve(_Layers(), "42", "q", state="cold", activation=None))
        return [(r["metadata"]["message_id"], round(r["blended_score"], 3)) for r in merged]
    return run


def _brain_cfg(monkeypatch, tmp_path, **cfg):
    monkeypatch.setattr(b, "CONFIG", {**b._DEFAULTS, "enabled": True, "db_path": str(tmp_path / "brain.sqlite"), **cfg})
    monkeypatch.setattr(b, "_instance", None)


def test_off_retrieve_is_unchanged_and_creates_no_store(monkeypatch, tmp_path, cold):
    monkeypatch.setattr(b, "CONFIG", {**b._DEFAULTS, "enabled": True, "prefetch_boost": False,
                                      "db_path": str(tmp_path / "x.sqlite")})
    monkeypatch.setattr(b, "_instance", None)
    assert cold() == [("a", 0.6), ("b", 0.58), ("c", 0.3)]
    assert not (tmp_path / "x.sqlite").exists()


def test_on_a_used_doc_overtakes_a_close_neighbour_not_a_far_one(monkeypatch, tmp_path, cold):
    _brain_cfg(monkeypatch, tmp_path, prefetch_boost=True)
    try:
        asyncio.run(usage.record(b.get_instance(), ["b", "c"]))
        asyncio.run(usage.record(b.get_instance(), ["b", "c"]))
        asyncio.run(usage.record(b.get_instance(), ["b", "c"]))                   # ~3 uses each → the full 0.05
        got = cold()
    finally:
        b._instance.close()
    assert [m for m, _ in got] == ["b", "a", "c"]                                  # b 0.58→0.63 passes a 0.60
    assert dict(got)["c"] == pytest.approx(0.35, abs=1e-3)                         # c gains 0.05 but stays last


def test_a_broken_brain_leaves_the_results_untouched(monkeypatch, tmp_path, cold):
    _brain_cfg(monkeypatch, tmp_path, prefetch_boost=True)

    async def boom(*a, **kw):
        raise RuntimeError("store down")
    monkeypatch.setattr(usage, "scores", boom)
    try:
        assert cold() == [("a", 0.6), ("b", 0.58), ("c", 0.3)]
    finally:
        if b._instance is not None:
            b._instance.close()
