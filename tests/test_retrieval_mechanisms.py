"""tests.test_retrieval_mechanisms — retrieve() surfaces mechanism attribution.

Spec §10.2: prefetch_bench.py needs, per result, which mechanism(s) found it.
retrieve()'s cold/drift paths now build labeled groups for merge_results, so
the RRF-fused output carries a ``mechanisms`` field with no further plumbing.
A dedicated minimal fake is used (not tests/_orch_fakes.py's _FakeLayers,
which predates GLiNER routing and lacks extract_query_entities).
"""
from __future__ import annotations

import asyncio

from memory.retrieval import retrieve


def _hit(message_id: str) -> dict:
    return {"content": message_id, "metadata": {"message_id": message_id}}


class _FakeLayers:
    """cold path: global search returns 'a', chat-scoped returns 'b', bm25 returns 'a'."""

    async def search_episodic_with_cache(self, chat_id, query, limit=5, chat_id_filter=None):
        if chat_id_filter is None:
            return [_hit("a")], False, "key:global"
        return [_hit("b")], False, "key:scoped"

    async def bm25_search(self, query, limit=15):
        return [_hit("a")]

    async def extract_query_entities(self, query):
        return {"entities": [], "entity_types": []}

    async def boost_by_entities(self, query, results, pre_extracted=None):
        return results

    async def rerank(self, query, results):
        return results

    async def bump_access(self, chat_id, ids):
        pass


def test_cold_retrieve_tags_each_result_with_its_mechanisms():
    layers = _FakeLayers()
    merged, _, _, _ = asyncio.run(
        retrieve(layers, "chat1", "query", state="cold", activation=None)
    )
    by_id = {r["metadata"]["message_id"]: r["mechanisms"] for r in merged}
    assert by_id["a"] == ["bm25", "semantic_global"]   # found by both
    assert by_id["b"] == ["semantic_chat_scoped"]       # found by one
