"""tests.test_memory_tools — search_memory tool routes through the real retrieve() pipeline.

Regression coverage for the bug fixed here: search_memory previously called
memory_layers.search_episodic(...) directly — a bare Chroma semantic search
with no RRF merge, no BM25/temporal fusion, no entity boosting, no
cross-encoder rerank. That made it a second, materially weaker retrieval path
than the one the prefetch daemon uses (memory.retrieval.retrieve), with the
exact same "might miss the right memory" failure mode search_memory exists to
catch as a last resort. It must now go through retrieve() too.

A dedicated minimal fake is used (not tests/_orch_fakes.py's _FakeLayers,
which predates GLiNER routing / retrieve()'s cold-path contract — see
tests/test_retrieval_mechanisms.py for the same pattern).
"""
from __future__ import annotations

import asyncio

from tools.memory_tools import build_search_memory_tool


def _hit(message_id: str, content: str, ts: float = 0.0) -> dict:
    return {"content": content, "metadata": {"message_id": message_id, "timestamp": ts}}


class _FakeLayers:
    """Bare search_episodic() surfaces an irrelevant top-1; the full retrieve()
    pipeline (BM25 fusion + cross-encoder rerank) promotes the relevant memory.

    This mirrors a real failure mode: raw Chroma cosine distance ranks a
    lexically-similar-but-wrong memory above the actually relevant one, which
    only BM25 term overlap and the cross-encoder can fix.
    """

    def __init__(self):
        self.bare_search_calls = 0
        self.bump_access_calls = 0

    async def search_episodic(self, query, limit=5, after=None, before=None,
                               topic_id=None, chat_id_filter=None):
        # What the OLD implementation called directly. On its own it only
        # ever surfaces the wrong memory.
        self.bare_search_calls += 1
        return [_hit("wrong", "irrelevant chatter about the weather", ts=100.0)]

    async def search_episodic_with_cache(self, chat_id, query, limit=5,
                                          topic_id=None, chat_id_filter=None):
        if chat_id_filter is None:
            return [_hit("wrong", "irrelevant chatter about the weather", ts=100.0)], False, "key:global"
        return [], False, "key:scoped"

    async def bm25_search(self, query, limit=15):
        # Only BM25 (term overlap) surfaces the actually relevant memory.
        return [_hit("right", "the decision the user actually asked about", ts=200.0)]

    async def extract_query_entities(self, query):
        return {"entities": [], "entity_types": []}

    async def boost_by_entities(self, query, results, pre_extracted=None):
        return results

    async def rerank(self, query, results):
        # Cross-encoder: correctly ranks the relevant memory first even
        # though raw semantic distance (search_episodic_with_cache) ranked
        # the irrelevant one first.
        return sorted(results, key=lambda r: r["metadata"]["message_id"] != "right")

    async def bump_access(self, chat_id, ids):
        self.bump_access_calls += 1


def test_search_memory_surfaces_the_bm25_only_result_reranked_first():
    """Proves the tool now goes through merge_results + rerank, not bare Chroma search.

    A bare search_episodic() call would return only 'wrong'. Through
    retrieve()'s merge (semantic + BM25) and cross-encoder rerank, 'right'
    (found only by BM25) must appear, and ranked ahead of 'wrong'.
    """
    layers = _FakeLayers()
    tool = build_search_memory_tool(layers)
    out = asyncio.run(tool.handler(query="what did we decide", chat_id="c1"))
    assert "the decision the user actually asked about" in out
    right_idx = out.index("the decision the user actually asked about")
    wrong_idx = out.index("irrelevant chatter about the weather")
    assert right_idx < wrong_idx


def test_search_memory_no_results_message_unchanged():
    class _EmptyLayers(_FakeLayers):
        async def search_episodic_with_cache(self, chat_id, query, limit=5,
                                              topic_id=None, chat_id_filter=None):
            return [], False, "key"

        async def bm25_search(self, query, limit=15):
            return []

    tool = build_search_memory_tool(_EmptyLayers())
    out = asyncio.run(tool.handler(query="nothing here", chat_id="c1"))
    assert out == "No relevant memories found."


def test_search_memory_after_before_still_filter_by_time():
    """External contract preserved: after/before still restrict the time window,
    now applied as a post-filter over the merged/boosted/reranked pool."""
    layers = _FakeLayers()
    tool = build_search_memory_tool(layers)
    # 'wrong' is at ts=100, 'right' is at ts=200 — excluding 'right' by an
    # upper bound should leave only 'wrong'.
    out = asyncio.run(tool.handler(
        query="what did we decide", chat_id="c1", before="1970-01-01T00:02:30Z",
    ))
    assert "irrelevant chatter about the weather" in out
    assert "the decision the user actually asked about" not in out


def test_search_memory_result_count_capped_at_tool_limit():
    """On-demand cap stays small (previous default was 5) even though
    retrieve()'s internal pool is larger — this is a deliberate, separate
    limit, not a regression."""
    from tools import memory_tools

    class _ManyLayers(_FakeLayers):
        async def search_episodic_with_cache(self, chat_id, query, limit=5,
                                              topic_id=None, chat_id_filter=None):
            if chat_id_filter is None:
                return [_hit(f"g{i}", f"global {i}", ts=float(i)) for i in range(10)], False, "key:global"
            return [], False, "key:scoped"

        async def bm25_search(self, query, limit=15):
            return []

        async def rerank(self, query, results):
            return results

    tool = build_search_memory_tool(_ManyLayers())
    out = asyncio.run(tool.handler(query="q", chat_id="c1"))
    assert len(out.splitlines()) == memory_tools._LIMIT
