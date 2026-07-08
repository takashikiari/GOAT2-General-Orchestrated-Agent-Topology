"""tests.test_backfill_enrichment — retroactive enrichment of existing L3 entries.

Real corpus measurement (2026-07-08): write-time enrichment (already shipped)
only affects entries written after the code runs — it does nothing for the
1780 entries that already existed. backfill_enrichment runs the same
enrich_l3_entry on a batch of already-stored entries, updating their metadata
in place via EpisodicMemory.update_metadata (a merge, not a replace — content/
chat_id/timestamp/tags are untouched).
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from scripts.backfill_enrichment import backfill_enrichment


def _entry(id_: str, content: str = "some content") -> dict:
    return {"id": id_, "content": content, "metadata": {}}


def test_backfill_enriches_every_entry():
    episodic = MagicMock()
    episodic.update_metadata = AsyncMock()
    extractor = MagicMock()
    extractor.extract = AsyncMock(return_value={
        "entities": ["GOAT"], "entity_types": ["project"], "memory_type": "fact",
    })
    entries = [_entry("a"), _entry("b"), _entry("c")]

    result = asyncio.run(backfill_enrichment(episodic, extractor, entries))

    assert result["total"] == 3
    assert episodic.update_metadata.call_count == 3
    updated_ids = {c.args[0] for c in episodic.update_metadata.call_args_list}
    assert updated_ids == {"a", "b", "c"}


def test_backfill_handles_empty_entries():
    episodic = MagicMock()
    episodic.update_metadata = AsyncMock()
    result = asyncio.run(backfill_enrichment(episodic, None, []))
    assert result["total"] == 0
    episodic.update_metadata.assert_not_called()


def test_backfill_respects_concurrency_limit():
    """A bounded semaphore, not unbounded asyncio.gather — GLiNER inference is
    CPU-bound; unbounded concurrency would thrash rather than speed things up."""
    episodic = MagicMock()
    max_concurrent = 0
    current = 0

    async def _slow_update(doc_id, updates):
        nonlocal current, max_concurrent
        current += 1
        max_concurrent = max(max_concurrent, current)
        await asyncio.sleep(0.01)
        current -= 1

    episodic.update_metadata = AsyncMock(side_effect=_slow_update)
    extractor = MagicMock()
    extractor.extract = AsyncMock(return_value={"entities": [], "entity_types": [], "memory_type": "conversation"})
    entries = [_entry(str(i)) for i in range(10)]

    asyncio.run(backfill_enrichment(episodic, extractor, entries, concurrency=2))

    assert max_concurrent <= 2


def test_backfill_one_failure_does_not_abort_batch():
    """enrich_l3_entry already degrades on internal failure without raising —
    confirm that property holds through the batch (one bad entry, others OK)."""
    episodic = MagicMock()

    async def _update(doc_id, updates):
        if doc_id == "bad":
            raise Exception("db error")

    episodic.update_metadata = AsyncMock(side_effect=_update)
    extractor = MagicMock()
    extractor.extract = AsyncMock(return_value={"entities": [], "entity_types": [], "memory_type": "conversation"})
    entries = [_entry("good1"), _entry("bad"), _entry("good2")]

    result = asyncio.run(backfill_enrichment(episodic, extractor, entries))

    assert result["total"] == 3
    assert episodic.update_metadata.call_count == 3
