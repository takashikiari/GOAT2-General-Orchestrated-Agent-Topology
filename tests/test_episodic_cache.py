"""Tests for Faza 2 Commit 2 — episodic recall cache.

Two test layers:

  1. Unit tests for ``EpisodicRecallCache`` (LRU + TTL + invalidation).
  2. Integration tests asserting that ``fetch_episodic_hits`` consults
     the cache before issuing ``mm.recall`` and that
     ``store_and_promote`` invalidates the cache after persisting.

All tests use the ``set_episodic_cache`` injector so they don't
share state across test functions.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from supervisor.session.episodic_cache import (
    EpisodicRecallCache,
    build_episodic_cache_key,
    get_episodic_cache,
    set_episodic_cache,
)


# ── Helpers ────────────────────────────────────────────────────────────────


def asyncio_run(coro):
    """Run a coroutine in a fresh loop (pytest-asyncio mode is 'auto')."""
    return asyncio.run(coro)


def _episodic_hit(content: str) -> SimpleNamespace:
    return SimpleNamespace(content=content)


def _mm_with_recall(hits: list | None = None, *, side_effect=None) -> MagicMock:
    """Mock MemoryManager that responds to ``recall(role, q, limit=...)``."""
    mm = MagicMock()
    if side_effect is not None:
        mm.recall = AsyncMock(side_effect=side_effect)
    else:
        mm.recall = AsyncMock(return_value=list(hits or []))
    return mm


@pytest.fixture(autouse=True)
def _fresh_cache():
    """Inject a brand-new cache for every test."""
    set_episodic_cache(EpisodicRecallCache(max_size=4, ttl_s=0.5))
    yield
    set_episodic_cache(None)


# ── Unit: key builder ─────────────────────────────────────────────────────


def test_build_key_normalizes_intent_case_and_whitespace():
    """intent is .strip().lower()-ed before becoming part of the key."""
    a = build_episodic_cache_key("  Hello WORLD  ", "goat", 5, 10)
    b = build_episodic_cache_key("hello world", "goat", 5, 10)
    assert a == b


def test_build_key_includes_role_limit_and_turn_bucket():
    """Different (role, limit, turn_bucket) produce different keys."""
    base = build_episodic_cache_key("intent", "goat", 5, 10)
    assert base != build_episodic_cache_key("intent", "user", 5, 10)
    assert base != build_episodic_cache_key("intent", "goat", 7, 10)
    # turn_bucket = turn // 5; turn=10 → bucket 2, turn=12 → bucket 2 (same).
    assert base == build_episodic_cache_key("intent", "goat", 5, 12)
    # turn=15 → bucket 3 (different).
    assert base != build_episodic_cache_key("intent", "goat", 5, 15)


def test_build_key_clamps_negative_turn_number_to_bucket_zero():
    """Negative turn numbers map to bucket 0 (no crash)."""
    k = build_episodic_cache_key("i", "goat", 5, -7)
    assert k == ("i", "goat", 5, 0)


def test_build_key_empty_intent_yields_empty_string():
    """An empty intent (after strip+lower) becomes the literal ''."""
    k = build_episodic_cache_key("", "goat", 5, 10)
    assert k == ("", "goat", 5, 2)


# ── Unit: cache behaviour ─────────────────────────────────────────────────


def test_cache_miss_returns_none_and_increments_counter():
    cache = EpisodicRecallCache(max_size=4, ttl_s=1.0)
    assert cache.get(("a",)) is None
    assert cache.misses == 1
    assert cache.hits == 0


def test_cache_put_then_get_returns_value_and_bumps_hit():
    cache = EpisodicRecallCache(max_size=4, ttl_s=1.0)
    cache.put(("a",), ["x"])
    assert cache.get(("a",)) == ["x"]
    assert cache.hits == 1
    assert cache.misses == 0


def test_cache_ttl_expiry_evicts_entry():
    cache = EpisodicRecallCache(max_size=4, ttl_s=0.05)
    cache.put(("a",), ["x"])
    time.sleep(0.06)
    assert cache.get(("a",)) is None
    assert cache.evictions_ttl == 1


def test_cache_lru_evicts_oldest_when_full():
    """Insertion past max_size evicts the LRU entry (FIFO of OrderedDict)."""
    cache = EpisodicRecallCache(max_size=2, ttl_s=10.0)
    cache.put(("a",), 1)
    cache.put(("b",), 2)
    cache.put(("c",), 3)  # overflows → "a" evicted
    assert cache.get(("a",)) is None
    assert cache.evictions_lru == 1
    assert cache.get(("b",)) == 2
    assert cache.get(("c",)) == 3


def test_cache_lru_bump_on_hit_protects_entry_from_eviction():
    """A hit moves the entry to MRU so it's not the next eviction victim."""
    cache = EpisodicRecallCache(max_size=2, ttl_s=10.0)
    cache.put(("a",), 1)
    cache.put(("b",), 2)
    assert cache.get(("a",)) == 1  # bumps "a" to MRU
    cache.put(("c",), 3)           # overflows → "b" is now LRU → evicted
    assert cache.get(("a",)) == 1
    assert cache.get(("b",)) is None


def test_cache_invalidate_drops_everything():
    cache = EpisodicRecallCache(max_size=4, ttl_s=10.0)
    cache.put(("a",), 1)
    cache.put(("b",), 2)
    cache.invalidate()
    assert cache.size == 0
    assert cache.get(("a",)) is None


def test_cache_constructor_rejects_zero_or_negative_size():
    with pytest.raises(ValueError):
        EpisodicRecallCache(max_size=0, ttl_s=1.0)
    with pytest.raises(ValueError):
        EpisodicRecallCache(max_size=4, ttl_s=0.0)


def test_singleton_setter_swaps_instance():
    set_episodic_cache(EpisodicRecallCache(max_size=8, ttl_s=2.0))
    a = get_episodic_cache()
    set_episodic_cache(EpisodicRecallCache(max_size=8, ttl_s=2.0))
    b = get_episodic_cache()
    assert a is not b  # replaced


# ── Integration: fetch_episodic_hits uses the cache ───────────────────────


def test_fetch_episodic_hits_uses_cache_on_second_call():
    """A second call with the same (intent, limit) must NOT re-invoke
    ``mm.recall`` — the cache must serve the first result."""
    from supervisor.session.memory_helpers import fetch_episodic_hits

    mm = _mm_with_recall([_episodic_hit("cached")])

    h1 = asyncio_run(fetch_episodic_hits(mm, "hello", 5, turn_number=10))
    h2 = asyncio_run(fetch_episodic_hits(mm, "hello", 5, turn_number=10))

    # Both calls return the same content.
    assert [h.content for h in h1] == ["cached"]
    assert [h.content for h in h2] == ["cached"]
    # But mm.recall was only invoked once.
    assert mm.recall.await_count == 1


def test_fetch_episodic_hits_bypasses_cache_on_different_intent():
    """A different intent must produce a fresh recall call (no false hit)."""
    from supervisor.session.memory_helpers import fetch_episodic_hits

    mm = _mm_with_recall([_episodic_hit("x")])
    asyncio_run(fetch_episodic_hits(mm, "hello", 5, turn_number=10))
    asyncio_run(fetch_episodic_hits(mm, "world", 5, turn_number=10))
    assert mm.recall.await_count == 2


def test_fetch_episodic_hits_bypasses_cache_on_different_limit():
    """Different `limit` → different key → fresh recall."""
    from supervisor.session.memory_helpers import fetch_episodic_hits

    mm = _mm_with_recall([_episodic_hit("x")])
    asyncio_run(fetch_episodic_hits(mm, "hello", 5, turn_number=10))
    asyncio_run(fetch_episodic_hits(mm, "hello", 7, turn_number=10))
    assert mm.recall.await_count == 2


def test_fetch_episodic_hits_bypasses_cache_on_different_turn_bucket():
    """turn_number // 5 shifts the bucket — different bucket → fresh recall."""
    from supervisor.session.memory_helpers import fetch_episodic_hits

    mm = _mm_with_recall([_episodic_hit("x")])
    asyncio_run(fetch_episodic_hits(mm, "hello", 5, turn_number=10))  # bucket 2
    asyncio_run(fetch_episodic_hits(mm, "hello", 5, turn_number=15))  # bucket 3
    assert mm.recall.await_count == 2


# ── Integration: store_and_promote invalidates the cache ─────────────────


def test_store_and_promote_invalidates_episodic_cache(monkeypatch):
    """After ``store_and_promote`` runs, the cache must be empty so
    the next recall observes the freshly-persisted turn."""
    from supervisor.session import turn_persistence
    from supervisor.session.episodic_cache import get_episodic_cache

    # Pre-populate the cache with a stale value.
    cache = get_episodic_cache()
    cache.put(("hello", "goat", 5, 0), [_episodic_hit("stale")])
    assert cache.size == 1

    # Build a minimal supervisor stub.
    supervisor = MagicMock()
    supervisor.memory_manager = MagicMock()
    supervisor._history = None
    supervisor._last_turn_result = None

    # schedule_promotion is fire-and-forget; stub it so the test
    # doesn't need a full ServiceRegistry.
    monkeypatch.setattr(
        turn_persistence, "schedule_promotion",
        lambda *a, **kw: None,
    )

    asyncio_run(turn_persistence.store_and_promote(supervisor, 1, "intent", "summary"))
    assert cache.size == 0
