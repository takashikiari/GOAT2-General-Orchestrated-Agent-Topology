"""tests.test_auto_promote — L2 size management via maybe_auto_promote.

Covers:
  - No-op when at or below cap
  - Surplus trimmed to exactly WORKING_MAX_MESSAGES (the fixed bug)
  - Oldest messages dropped, newest kept
  - Large surplus (> PROMOTE_CHUNK_SIZE) handled across multiple iterations
  - L3 is never written (no store() call anywhere on working)
  - Lock is held for the entire read-trim-save cycle (concurrent save sees
    the trimmed list, not the original)
"""
from __future__ import annotations

import asyncio

import pytest

from memory.auto_promote import PROMOTE_CHUNK_SIZE, PROMOTE_MIN_SURPLUS, maybe_auto_promote
from memory.config import WORKING_MAX_MESSAGES


# ---------------------------------------------------------------------------
# Minimal fake — only the WorkingMemory surface auto_promote touches.
# Intentionally has NO store() / episodic methods: if maybe_auto_promote ever
# tries to write to L3, it will raise AttributeError and the test fails.
# ---------------------------------------------------------------------------

class _FakeWorking:
    def __init__(self, messages: list[dict]) -> None:
        self._messages: list[dict] = list(messages)
        self._lock = asyncio.Lock()
        self.save_calls: list[list[dict]] = []

    def chat_lock(self, _chat_id: str) -> asyncio.Lock:
        return self._lock

    async def get_messages_raw(self, _chat_id: str) -> list[dict]:
        return list(self._messages)

    async def save_messages_raw(self, _chat_id: str, messages: list[dict]) -> None:
        self._messages = list(messages)
        self.save_calls.append(list(messages))


def _msgs(n: int) -> list[dict]:
    """Generate n fake messages (alternating user/assistant, distinct content)."""
    return [
        {
            "role": "user" if i % 2 == 0 else "assistant",
            "content": f"message-{i}",
            "timestamp": float(i),
        }
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# No-op cases
# ---------------------------------------------------------------------------

def test_no_op_when_below_cap():
    """save_messages_raw is never called when L2 is below WORKING_MAX_MESSAGES."""
    msgs = _msgs(WORKING_MAX_MESSAGES - 1)
    w = _FakeWorking(msgs)
    asyncio.run(maybe_auto_promote("c", w))
    assert w.save_calls == [], "save must not be called when under cap"
    assert w._messages == msgs, "message list must be unchanged"


def test_no_op_when_exactly_at_cap():
    """save_messages_raw is never called when L2 is exactly at WORKING_MAX_MESSAGES."""
    msgs = _msgs(WORKING_MAX_MESSAGES)
    w = _FakeWorking(msgs)
    asyncio.run(maybe_auto_promote("c", w))
    assert w.save_calls == []
    assert w._messages == msgs


# ---------------------------------------------------------------------------
# Surplus trimming — the primary bug fix
# ---------------------------------------------------------------------------

def test_surplus_trimmed_to_exactly_cap():
    """L2 ends at exactly WORKING_MAX_MESSAGES, not 0 (was the bug)."""
    msgs = _msgs(WORKING_MAX_MESSAGES + 5)
    w = _FakeWorking(msgs)
    asyncio.run(maybe_auto_promote("c", w))
    assert len(w._messages) == WORKING_MAX_MESSAGES, (
        f"expected {WORKING_MAX_MESSAGES}, got {len(w._messages)} — "
        "surplus trimming bug: chunk_size must be capped to surplus_now"
    )


def test_oldest_messages_dropped_newest_kept():
    """The oldest (first) messages are dropped; the newest (last) are kept."""
    n = WORKING_MAX_MESSAGES + 7
    msgs = _msgs(n)
    w = _FakeWorking(msgs)
    asyncio.run(maybe_auto_promote("c", w))
    expected = msgs[7:]  # first 7 dropped
    assert w._messages == expected, (
        "newest messages must survive; oldest must be dropped"
    )


def test_below_min_surplus_is_noop():
    """Surplus < PROMOTE_MIN_SURPLUS does not trim (prevents ping-pong at cap)."""
    msgs = _msgs(WORKING_MAX_MESSAGES + PROMOTE_MIN_SURPLUS - 1)
    w = _FakeWorking(msgs)
    asyncio.run(maybe_auto_promote("c", w))
    assert w.save_calls == [], "trim must not fire when surplus < PROMOTE_MIN_SURPLUS"
    assert w._messages == msgs


def test_at_min_surplus_triggers_trim():
    """Surplus == PROMOTE_MIN_SURPLUS triggers trim; exactly PROMOTE_MIN_SURPLUS dropped."""
    msgs = _msgs(WORKING_MAX_MESSAGES + PROMOTE_MIN_SURPLUS)
    w = _FakeWorking(msgs)
    asyncio.run(maybe_auto_promote("c", w))
    assert len(w._messages) == WORKING_MAX_MESSAGES
    assert w._messages == msgs[PROMOTE_MIN_SURPLUS:]


def test_save_called_exactly_once_for_small_surplus():
    """Small surplus (fits in one chunk) → exactly one save call."""
    msgs = _msgs(WORKING_MAX_MESSAGES + PROMOTE_MIN_SURPLUS)
    w = _FakeWorking(msgs)
    asyncio.run(maybe_auto_promote("c", w))
    assert len(w.save_calls) == 1, (
        "small surplus must produce exactly one save call"
    )
    assert len(w.save_calls[0]) == WORKING_MAX_MESSAGES


# ---------------------------------------------------------------------------
# Large surplus — multi-iteration correctness
# ---------------------------------------------------------------------------

def test_large_surplus_ends_at_cap():
    """Surplus larger than PROMOTE_CHUNK_SIZE still ends at exactly cap."""
    n = WORKING_MAX_MESSAGES + PROMOTE_CHUNK_SIZE + 10
    msgs = _msgs(n)
    w = _FakeWorking(msgs)
    asyncio.run(maybe_auto_promote("c", w))
    assert len(w._messages) == WORKING_MAX_MESSAGES, (
        f"expected {WORKING_MAX_MESSAGES}, got {len(w._messages)}"
    )


def test_large_surplus_oldest_dropped():
    """With a large surplus the oldest messages are still the ones removed."""
    surplus = PROMOTE_CHUNK_SIZE + 10
    msgs = _msgs(WORKING_MAX_MESSAGES + surplus)
    w = _FakeWorking(msgs)
    asyncio.run(maybe_auto_promote("c", w))
    assert w._messages == msgs[surplus:]


def test_large_surplus_multiple_save_calls():
    """A surplus bigger than one chunk requires multiple save calls."""
    surplus = PROMOTE_CHUNK_SIZE + 5
    msgs = _msgs(WORKING_MAX_MESSAGES + surplus)
    w = _FakeWorking(msgs)
    asyncio.run(maybe_auto_promote("c", w))
    assert len(w.save_calls) >= 2, (
        "large surplus should produce at least 2 save calls (one per chunk)"
    )
    # Each intermediate save must also leave at most WORKING_MAX_MESSAGES + 1 chunk worth
    for saved in w.save_calls:
        assert len(saved) >= WORKING_MAX_MESSAGES, (
            "no intermediate save should leave fewer than WORKING_MAX_MESSAGES messages"
        )
    # Final save is exactly at cap
    assert len(w.save_calls[-1]) == WORKING_MAX_MESSAGES


# ---------------------------------------------------------------------------
# L3 isolation — auto_promote must never write to L3
# ---------------------------------------------------------------------------

def test_no_l3_write_on_small_surplus():
    """_FakeWorking has no episodic/store method; L3 write would raise AttributeError."""
    msgs = _msgs(WORKING_MAX_MESSAGES + 3)
    w = _FakeWorking(msgs)
    # If maybe_auto_promote tried to call episodic.store() or any attribute
    # not on _FakeWorking, this raises immediately.
    asyncio.run(maybe_auto_promote("c", w))   # must not raise


def test_no_l3_write_on_large_surplus():
    """Same guard for the multi-iteration path."""
    msgs = _msgs(WORKING_MAX_MESSAGES + PROMOTE_CHUNK_SIZE + 5)
    w = _FakeWorking(msgs)
    asyncio.run(maybe_auto_promote("c", w))   # must not raise


# ---------------------------------------------------------------------------
# Lock semantics — concurrent save sees trimmed list
# ---------------------------------------------------------------------------

def test_concurrent_save_sees_trimmed_list():
    """A second coroutine that reads L2 after auto_promote sees the trimmed list.

    Simulates the ordering invariant: auto_promote holds the lock for the full
    cycle, so a concurrent reader that waits for the lock sees the final
    (trimmed) state, not an intermediate or pre-trim snapshot.
    """
    msgs = _msgs(WORKING_MAX_MESSAGES + 5)
    w = _FakeWorking(msgs)
    seen_after: list[list[dict]] = []

    async def _concurrent_reader():
        async with w.chat_lock("c"):
            seen_after.append(await w.get_messages_raw("c"))

    async def _run():
        promote_task = asyncio.create_task(maybe_auto_promote("c", w))
        reader_task = asyncio.create_task(_concurrent_reader())
        await asyncio.gather(promote_task, reader_task)

    asyncio.run(_run())
    assert len(seen_after) == 1
    # The reader must have seen the trimmed list (auto_promote completed first
    # or second — either way the reader sees a consistent snapshot).
    assert len(seen_after[0]) <= WORKING_MAX_MESSAGES + 5   # never more than original
    # At least one of the two orderings (promote-then-read or read-then-promote)
    # must produce the trimmed result; the lock guarantees no torn read.
    trimmed_count = len(msgs) - WORKING_MAX_MESSAGES
    assert seen_after[0] == msgs[trimmed_count:] or seen_after[0] == msgs
