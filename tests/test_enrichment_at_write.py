"""tests.test_enrichment_at_write — MemoryLayers enriches L3 entries at write time.

Real corpus measurement (2026-07-08): only ~10% of stored entries ever had
entities/entity_types/memory_type/importance populated in metadata, because
enrichment only ran at L2-trim time (auto_promote.pair_and_enrich_dropped),
which most directly-archived turns never pass through — confirmed as the
reason entity_boost is silently inert for ~90% of real retrieval candidates
(it reads meta.get("entities"), empty for unenriched entries). store_episodic
now fires enrichment immediately after every write, fire-and-forget but
tracked in _pending_bg so tests (and shutdown) can await it deterministically
via the existing drain_background().
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from memory.layers import MemoryLayers


def _make_layers(extractor=None):
    working = MagicMock()
    episodic = MagicMock()
    episodic.store = AsyncMock(return_value="doc-1")
    episodic.update_metadata = AsyncMock()
    permanent = MagicMock()
    layers = MemoryLayers(working, episodic, permanent, extractor=extractor)
    return layers, episodic


async def _store_and_drain(layers, content="GOAT is a memory system"):
    doc_id = await layers.store_episodic("chat1", content)
    await layers.drain_background()
    return doc_id


def test_store_episodic_enriches_at_write_time():
    extractor = MagicMock()
    extractor.extract = AsyncMock(return_value={
        "entities": ["GOAT"], "entity_types": ["project"], "memory_type": "fact",
    })
    layers, episodic = _make_layers(extractor=extractor)

    doc_id = asyncio.run(_store_and_drain(layers))

    episodic.update_metadata.assert_called_once()
    call_args = episodic.update_metadata.call_args
    assert call_args[0][0] == doc_id
    updates = call_args[0][1]
    assert updates["entities"] == "GOAT"
    assert updates["memory_type"] == "fact"


def test_store_episodic_enrichment_failure_does_not_break_store():
    """Enrichment is fire-and-forget and best-effort; a failure there must
    never surface to the store_episodic caller, which already has its doc_id."""
    layers, episodic = _make_layers(extractor=None)
    episodic.update_metadata = AsyncMock(side_effect=Exception("db error"))

    doc_id = asyncio.run(_store_and_drain(layers))

    assert doc_id == "doc-1"


def test_store_episodic_enrichment_is_tracked_in_pending_bg():
    """The enrichment task must be awaitable via drain_background — not
    fire-and-forget-and-lost — so shutdown/tests see it deterministically."""
    layers, _episodic = _make_layers(extractor=None)

    async def _run():
        await layers.store_episodic("chat1", "some content")
        assert len(layers._pending_bg) >= 1
        await layers.drain_background()
        assert len(layers._pending_bg) == 0

    asyncio.run(_run())


def test_store_episodic_enrichment_uses_no_extractor_fallback():
    layers, episodic = _make_layers(extractor=None)

    asyncio.run(_store_and_drain(layers))

    updates = episodic.update_metadata.call_args[0][1]
    assert updates["memory_type"] == "conversation"
    assert updates["entities"] == ""
