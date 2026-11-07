import asyncio

from memory.content_filter import alnum_count, drop_tiny


def test_alnum_counts_letters_digits_any_script():
    assert alnum_count("🤦") == 0 and alnum_count("Ce?") == 2 and alnum_count("în 2026") == 6
    assert alnum_count(None) == 0


def test_drop_tiny_keeps_real_entries():
    rs = [{"content": "🤦"}, {"content": "Ce?"}, {"content": "🤣🤣🤣"}, {"content": "Letta pe 8283"}, {"content": None}]
    assert [r["content"] for r in drop_tiny(rs, 3)] == ["Letta pe 8283"]
    assert drop_tiny(rs, 0) == rs


def _ent(mid, content):
    return {"content": content, "metadata": {"message_id": mid}}


class _NoRerankLayers:
    """Cold path whose global search returns a tiny entry first; no cross-encoder at all."""

    def __init__(self):
        self.merge_inputs = None

    async def search_episodic_with_cache(self, chat_id, query, limit=5, chat_id_filter=None):
        return ([_ent("t", "🤦"), _ent("g", "despre serverul Letta")] if chat_id_filter is None
                else [_ent("s", "Ce?")]), False, "k"

    async def bm25_search(self, query, limit=15):
        return [_ent("t", "🤦")]

    async def extract_query_entities(self, query):
        return {"entities": [], "entity_types": []}

    async def boost_by_entities(self, query, results, pre_extracted=None):
        return results

    async def rerank(self, query, results):  # stands in for "reranker unavailable": returns input unchanged
        return results

    async def bump_access(self, chat_id, ids):
        pass


def test_filter_runs_before_the_merge_and_without_a_reranker(monkeypatch):
    """Moved from layers.rerank (2026-11-07): tiny entries never reach merge_results, reranker or not."""
    import memory.config as cfg
    import memory.retrieval as rt
    seen = []
    real_merge = rt.merge_results
    monkeypatch.setattr(rt, "merge_results", lambda groups: seen.append(groups) or real_merge(groups))
    monkeypatch.setattr(cfg, "L3_MIN_ALNUM", 5)
    merged, *_ = asyncio.run(rt.retrieve(_NoRerankLayers(), "c", "q", state="cold", activation=None))
    assert [r["content"] for r in merged] == ["despre serverul Letta"]
    assert all(alnum_count(r["content"]) >= 5 for _, rs in seen[-1] for r in rs)  # filtered before the merge
    monkeypatch.setattr(cfg, "L3_MIN_ALNUM", 0)
    merged, *_ = asyncio.run(rt.retrieve(_NoRerankLayers(), "c", "q", state="cold", activation=None))
    assert {r["content"] for r in merged} == {"🤦", "despre serverul Letta", "Ce?"}  # 0 = off


def test_warm_path_is_filtered_too(monkeypatch):
    import memory.config as cfg
    import memory.retrieval as rt
    from types import SimpleNamespace
    monkeypatch.setattr(cfg, "L3_MIN_ALNUM", 5)
    act = SimpleNamespace(merged=[{"content": "🤦", "metadata": {"timestamp": 1.0}},
                                  {"content": "despre serverul Letta", "metadata": {"timestamp": 1.0}}])
    merged, *_ = asyncio.run(rt.retrieve(_NoRerankLayers(), "c", "q", state="warm", activation=act))
    assert [r["content"] for r in merged] == ["despre serverul Letta"]


def test_rerank_no_longer_filters(monkeypatch):
    """Single source of truth: the filter is applied before the merge, not again inside layers.rerank."""
    import memory.config as cfg
    from memory.layers import MemoryLayers
    seen = []

    class _R:
        async def rerank(self, q, results):
            seen.append([r["content"] for r in results])
            return results

    layers = object.__new__(MemoryLayers)
    layers._reranker, layers._episodic = _R(), None
    for k, v in (("CHUNKS_SEARCH", False), ("CHUNKS_HIGHLIGHTS", False), ("L3_MIN_ALNUM", 5), ("L3_WAVE2", False)):
        monkeypatch.setattr(cfg, k, v)
    asyncio.run(layers.rerank("q", [{"content": "🤦"}, {"content": "despre Letta"}]))
    assert seen[-1] == ["🤦", "despre Letta"]


def test_threshold_5_boundaries():
    """T7 at l3_min_alnum = 5 (Laris, 2026-11-07): letters/digits counted, >= 5 passes."""
    rs = [{"content": c} for c in ("Push", "Gata", "Da ok", "Ce?", "Super", "Gata!!", "Super 1", "   ", "?!... ,,")]
    kept = [r["content"] for r in drop_tiny(rs, 5)]
    assert kept == ["Super", "Super 1"]  # 4 ("Push", "Gata", "Da ok", "Gata!!") drops, 5 passes, 6 passes
    assert [alnum_count(c) for c in ("Gata!!", "Super", "Super 1", "   ", "?!... ,,")] == [4, 5, 6, 0, 0]


def test_live_config_threshold_is_5():
    import memory.config as cfg
    assert cfg.L3_MIN_ALNUM == 5
