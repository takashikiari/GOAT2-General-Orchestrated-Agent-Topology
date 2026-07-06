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
    """Dropped user+assistant pair with l3_id triggers enrich_l3_entry."""
    from memory.auto_promote import maybe_auto_promote
    from memory.config import WORKING_MAX_MESSAGES
    # Build messages: 2 dropped + WORKING_MAX_MESSAGES kept
    dropped = [
        {"role": "user", "content": "hello", "l3_id": "doc-001"},
        {"role": "assistant", "content": "hi there", "l3_id": "doc-001"},
    ]
    kept = [{"role": "user", "content": f"msg{i}"} for i in range(WORKING_MAX_MESSAGES)]
    working = _make_working(dropped + kept)
    episodic = MagicMock()
    episodic.update_metadata = AsyncMock()
    asyncio.run(maybe_auto_promote("chat1", working, episodic=episodic, extractor=None))
    episodic.update_metadata.assert_called()
    call_args = episodic.update_metadata.call_args_list[0]
    assert call_args[0][0] == "doc-001"


def test_enrichment_skips_messages_without_l3_id():
    """Messages without l3_id (old format) are dropped but not enriched."""
    from memory.auto_promote import maybe_auto_promote
    from memory.config import WORKING_MAX_MESSAGES
    dropped = [
        {"role": "user", "content": "old message"},
        {"role": "assistant", "content": "old reply"},
    ]
    kept = [{"role": "user", "content": f"msg{i}"} for i in range(WORKING_MAX_MESSAGES)]
    working = _make_working(dropped + kept)
    episodic = MagicMock()
    episodic.update_metadata = AsyncMock()
    asyncio.run(maybe_auto_promote("chat1", working, episodic=episodic, extractor=None))
    episodic.update_metadata.assert_not_called()
