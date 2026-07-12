"""tests.test_layers_bm25_sync — BM25's cached metadata must stay in sync with
ChromaDB's copy for the same L3 entry.

Two confirmed bugs (direct code trace + empirical reproduction against real
production data, 2026-07-12):

1. ``MemoryLayers.store_episodic`` builds a ``metadata`` dict, calls
   ``EpisodicMemory.store`` (which stamps ``message_id`` on ITS OWN internal
   copy right before writing to Chroma — never propagated back), then spreads
   the original, still-message_id-less ``metadata`` dict into
   ``BM25Index.add_doc``. Every BM25-indexed copy of every memory permanently
   lacks ``message_id``, which breaks dedup identity in
   ``memory.result_merger._result_id`` (see tests/test_result_merger.py).

2. ``_enrich_at_write``'s async enrichment updates ChromaDB metadata via
   ``EpisodicMemory.update_metadata`` but never touches BM25's cached copy, so
   ``entity_boost``'s entity-overlap boosting can never fire for a result
   recovered only via BM25 keyword match.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from memory.bm25_index import BM25Index
from memory.layers import MemoryLayers


def _make_layers(bm25, extractor=None):
    working = MagicMock()
    episodic = MagicMock()
    episodic.store = AsyncMock(return_value="doc-1")
    episodic.update_metadata = AsyncMock()
    permanent = MagicMock()
    layers = MemoryLayers(working, episodic, permanent, extractor=extractor, bm25=bm25)
    return layers, episodic


def test_store_episodic_bm25_metadata_includes_message_id():
    """bm25.add_doc must receive metadata carrying the real doc_id as message_id,
    matching what EpisodicMemory.store wrote to ChromaDB — not the pre-stamp dict."""
    bm25 = MagicMock()
    layers, episodic = _make_layers(bm25)

    async def _run():
        doc_id = await layers.store_episodic("chat1", "hello world")
        await layers.drain_background()
        return doc_id

    doc_id = asyncio.run(_run())

    assert doc_id == "doc-1"
    bm25.add_doc.assert_called_once()
    passed_doc_id, _content, passed_metadata = bm25.add_doc.call_args[0]
    assert passed_doc_id == doc_id
    assert passed_metadata["message_id"] == doc_id


def test_store_episodic_bm25_cached_entry_matches_chromadb_message_id():
    """End-to-end with the real BM25Index class: after store_episodic, the
    BM25-cached metadata for the doc must carry the same message_id ChromaDB has."""
    bm25 = BM25Index(episodic=MagicMock())
    layers, episodic = _make_layers(bm25)

    async def _run():
        doc_id = await layers.store_episodic("chat1", "hello world")
        await layers.drain_background()
        return doc_id

    doc_id = asyncio.run(_run())

    cached = next(d for d in bm25._docs if d["id"] == doc_id)
    assert cached["metadata"]["message_id"] == doc_id


def test_enrichment_updates_bm25_cached_metadata():
    """After _enrich_at_write runs, BM25's cached metadata for the doc must
    reflect the enriched fields (entities/importance/memory_type), not just
    ChromaDB's — otherwise entity_boost can never fire on BM25-only hits."""
    extractor = MagicMock()
    extractor.extract = AsyncMock(return_value={
        "entities": ["GOAT"], "entity_types": ["project"], "memory_type": "fact",
    })
    bm25 = BM25Index(episodic=MagicMock())
    layers, episodic = _make_layers(bm25, extractor=extractor)

    async def _run():
        doc_id = await layers.store_episodic("chat1", "GOAT is a memory system")
        await layers.drain_background()
        return doc_id

    doc_id = asyncio.run(_run())

    cached = next(d for d in bm25._docs if d["id"] == doc_id)
    assert cached["metadata"]["entities"] == "GOAT"
    assert cached["metadata"]["entity_types"] == "project"
    assert cached["metadata"]["memory_type"] == "fact"
    assert "importance" in cached["metadata"]
