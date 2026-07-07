"""tests.test_auto_promote_enrichment — enrichment fires for dropped messages with l3_id."""
from __future__ import annotations
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


def _make_working(messages):
    working = MagicMock()
    working.chat_lock = MagicMock(return_value=MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock()))
    working.get_messages_raw = AsyncMock(return_value=messages)
    working.save_messages_raw = AsyncMock()
    return working


def test_no_enrichment_when_no_surplus():
    """When under cap, no trim and no enrichment."""
    from memory.auto_promote import maybe_auto_promote
    from memory.config import WORKING_MAX_MESSAGES
    messages = [{"role": "user", "content": "hi"} for _ in range(5)]
    working = _make_working(messages)
    episodic = MagicMock()
    episodic.update_metadata = AsyncMock()
    asyncio.run(maybe_auto_promote("chat1", working, episodic=episodic, extractor=None))
    episodic.update_metadata.assert_not_called()


def test_enrichment_fires_for_dropped_pair_with_l3_id():
    """Dropped pairs with l3_id trigger enrich_l3_entry once surplus >= PROMOTE_MIN_SURPLUS."""
    from memory.auto_promote import PROMOTE_MIN_SURPLUS, maybe_auto_promote
    from memory.config import WORKING_MAX_MESSAGES
    # Need surplus >= PROMOTE_MIN_SURPLUS (4) to trigger trim.
    # Use 2 user+assistant pairs = 4 messages dropped, all with l3_ids.
    dropped = [
        {"role": "user", "content": "hello1", "l3_id": "doc-001"},
        {"role": "assistant", "content": "hi there1", "l3_id": "doc-001"},
        {"role": "user", "content": "hello2", "l3_id": "doc-002"},
        {"role": "assistant", "content": "hi there2", "l3_id": "doc-002"},
    ]
    kept = [{"role": "user", "content": f"msg{i}"} for i in range(WORKING_MAX_MESSAGES)]
    assert len(dropped) >= PROMOTE_MIN_SURPLUS, "test setup must satisfy surplus threshold"
    working = _make_working(dropped + kept)
    episodic = MagicMock()
    episodic.update_metadata = AsyncMock()
    asyncio.run(maybe_auto_promote("chat1", working, episodic=episodic, extractor=None))
    episodic.update_metadata.assert_called()
    enriched_ids = {c[0][0] for c in episodic.update_metadata.call_args_list}
    assert "doc-001" in enriched_ids
    assert "doc-002" in enriched_ids


def test_enrichment_skips_messages_without_l3_id():
    """Messages without l3_id (old format) are dropped but not enriched."""
    from memory.auto_promote import PROMOTE_MIN_SURPLUS, maybe_auto_promote
    from memory.config import WORKING_MAX_MESSAGES
    # 4 dropped messages without l3_id (surplus = PROMOTE_MIN_SURPLUS).
    dropped = [
        {"role": "user", "content": f"old message {i}"}
        for i in range(PROMOTE_MIN_SURPLUS)
    ]
    kept = [{"role": "user", "content": f"msg{i}"} for i in range(WORKING_MAX_MESSAGES)]
    working = _make_working(dropped + kept)
    episodic = MagicMock()
    episodic.update_metadata = AsyncMock()
    asyncio.run(maybe_auto_promote("chat1", working, episodic=episodic, extractor=None))
    episodic.update_metadata.assert_not_called()
