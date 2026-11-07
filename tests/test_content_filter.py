import asyncio

from memory.content_filter import alnum_count, drop_tiny


def test_alnum_counts_letters_digits_any_script():
    assert alnum_count("🤦") == 0 and alnum_count("Ce?") == 2 and alnum_count("în 2026") == 6
    assert alnum_count(None) == 0


def test_drop_tiny_keeps_real_entries():
    rs = [{"content": "🤦"}, {"content": "Ce?"}, {"content": "🤣🤣🤣"}, {"content": "Letta pe 8283"}, {"content": None}]
    assert [r["content"] for r in drop_tiny(rs, 3)] == ["Letta pe 8283"]
    assert drop_tiny(rs, 0) == rs


def test_layers_rerank_filters_before_scoring_only_when_enabled(monkeypatch):
    import memory.config as cfg
    from memory.layers import MemoryLayers
    seen = []

    class _R:
        async def rerank(self, q, results):
            seen.append([r["content"] for r in results])
            return results

    layers = object.__new__(MemoryLayers)
    layers._reranker, layers._episodic = _R(), None
    for k, v in (("CHUNKS_SEARCH", False), ("CHUNKS_HIGHLIGHTS", False), ("L3_MIN_ALNUM", 3)):
        monkeypatch.setattr(cfg, k, v)
    asyncio.run(layers.rerank("q", [{"content": "🤦"}, {"content": "despre Letta"}]))
    assert seen[-1] == ["despre Letta"]
    assert asyncio.run(layers.rerank("q", [{"content": "🤦"}])) == []
    monkeypatch.setattr(cfg, "L3_MIN_ALNUM", 0)
    asyncio.run(layers.rerank("q", [{"content": "🤦"}]))
    assert seen[-1] == ["🤦"]
