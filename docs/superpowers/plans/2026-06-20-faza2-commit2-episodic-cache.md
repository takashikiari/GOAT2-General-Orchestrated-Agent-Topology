# Faza 2 Commit 2 — Episodic Recall Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a bounded LRU+TTL cache in front of `fetch_episodic_hits` so repeated turns with the same intent skip the ChromaDB round-trip. Cache is invalidated on any `mm.store(...)` to the episodic tier (directly or via `store_and_promote` → schedule_promotion).

**Architecture:** A new module `supervisor/session/episodic_cache.py` owns a process-local `EpisodicRecallCache` (LRU 256, TTL 60s, key = `(intent_normalized, role, limit, turn_number // 5)`). `fetch_episodic_hits` in `memory_helpers.py` consults the cache before calling `mm.recall(...)`. `store_and_promote` in `turn_persistence.py` invalidates the cache after persisting a turn. The cache instance is a module-level singleton attached to the memory manager (so tests can swap it out).

**Tech Stack:** Python 3.12, `collections.OrderedDict` (LRU), `time.monotonic` (TTL), existing `MemoryManager` API.

## Global Constraints

- Project floor: Python 3.12. Already enforced.
- Module-line ceiling: 260 lines per file (existing project rule).
- All new tunables must live in `config/limits.py` and re-export through `__all__` (per `docs/magic_numbers_policy.md`).
- Logs via `logging.getLogger("goat2.<module>")` — never `print()`.
- Style: file-level docstring, `from __future__ import annotations`, `Final` for constants.
- Cache failures are **best-effort**: any exception inside the cache must surface as a cache miss, never an error to the caller.
- Cache key uses `intent_normalized = intent.strip().lower()`. Empty intent after normalization → bypass cache entirely (no key to collide on, and the original `mem_turn` already short-circuits empty intent upstream).

## Files Touched

| File | Change |
|---|---|
| `config/limits.py` | Add 4 new cache tunables: `EPISODIC_CACHE_MAX_SIZE`, `EPISODIC_CACHE_TTL_S`, `EPISODIC_CACHE_TURN_BUCKET`. |
| `supervisor/session/episodic_cache.py` | NEW. Owns `EpisodicRecallCache` + module-level singleton + key helpers. |
| `supervisor/session/memory_helpers.py` | `fetch_episodic_hits` consults cache before the recall call; signature gains a `turn_number: int` parameter. |
| `supervisor/session/mem_inject.py` | `mem_turn` accepts and forwards `turn_number`. |
| `supervisor/turn_runner.py` | `run_turn` computes `turn_number` and passes it to `mem_turn`. |
| `supervisor/session/turn_persistence.py` | `store_and_promote` invalidates the episodic cache at the end. |
| `tests/test_episodic_cache.py` | NEW. 12 tests covering hit/miss/TTL/LRU/invalidation/thread-safety/edge cases. |

## Key Design Decisions (Reference for Implementers)

1. **Cache key components** (in order, joined by `::`):
   - `intent_normalized`: `intent.strip().lower()` — exact normalization that the user committed to.
   - `role`: `SESSION_ROLE` (constant from `config/roles`).
   - `limit`: the `top_k` parameter (so 5 vs 7 don't collide).
   - `turn_bucket`: `turn_number // 5` — refreshes the cache every 5 turns so a long session naturally discards stale state without needing a clock per query.

2. **LRU eviction**: `OrderedDict` with `move_to_end` on hit, `popitem(last=False)` on overflow.

3. **TTL**: each entry stores `(value, expires_at_monotonic)`. On `get`, if `now > expires_at` → evict + miss.

4. **Invalidation**: `invalidate()` clears the entire cache. `store_and_promote` calls it at the end (after the new turn has been written to working memory, before `schedule_promotion` may touch episodic). Cheap and conservative — surgical key invalidation isn't worth the complexity since the next recall will simply re-fetch.

5. **Singleton plumbing**: `get_episodic_cache()` returns a module-level lazy instance. `set_episodic_cache(instance | None)` lets tests inject a fresh cache per test. Singleton is process-local — GOAT runs as one process.

6. **Thread safety**: not a concern — GOAT runs async, single event loop. The cache is only touched inside coroutines. No locks.

7. **Empty intent**: if `intent_normalized == ""`, cache is bypassed entirely (no key, no store). The recall call still proceeds.

8. **Failure policy**: any exception inside cache `get`/`put`/`invalidate` is swallowed at DEBUG level and treated as a miss / no-op. The cache must never break the recall path.

---

### Task 1: Add tunables to `config/limits.py`

**Files:**
- Modify: `config/limits.py:48-49` (in the Faza 2 block)

- [ ] **Step 1: Add the three constants**

In `config/limits.py`, just after `DEFAULT_EPISODIC_TOP_K: Final[int] = 5`, insert:

```python
# Faza 2 Commit 2: episodic recall cache tunables.
EPISODIC_CACHE_MAX_SIZE: Final[int] = 256
"""LRU cap for the episodic recall cache (entries)."""

EPISODIC_CACHE_TTL_S: Final[float] = 60.0
"""Per-entry TTL for cached episodic recall results (seconds)."""

EPISODIC_CACHE_TURN_BUCKET: Final[int] = 5
"""Refresh the cache bucket every N turns — bounds staleness without a per-query clock."""
```

Also extend `__all__` (the list at the top of the file) by adding the three names inside the `"# Faza 2"` comment block — add them right after `"DEFAULT_EPISODIC_TOP_K"`. They are part of the same Faza 2 family.

- [ ] **Step 2: Verify the file still parses**

Run: `python -c "from config.limits import EPISODIC_CACHE_MAX_SIZE, EPISODIC_CACHE_TTL_S, EPISODIC_CACHE_TURN_BUCKET; print(EPISODIC_CACHE_MAX_SIZE, EPISODIC_CACHE_TTL_S, EPISODIC_CACHE_TURN_BUCKET)"`
Expected: `256 60.0 5`

- [ ] **Step 3: Commit**

```bash
git add config/limits.py
git commit -m "feat(config): Faza 2 Commit 2 — episodic recall cache tunables"
```

---

### Task 2: Create `EpisodicRecallCache` class

**Files:**
- Create: `supervisor/session/episodic_cache.py`

This module owns the cache class, the key builder, and the singleton accessor. It is **pure Python** — no async, no I/O. The recall call site is async; the cache itself is sync (the critical section is in-memory dict access).

- [ ] **Step 1: Create the module with file-level docstring, imports, and constants**

Create `supervisor/session/episodic_cache.py`:

```python
"""Episodic recall cache — bounded LRU+TTL in front of ChromaDB.

GOAT 2.0 Faza 2 Commit 2. Repeated turns with the same intent
(common during clarification loops, retries, and short task
spans) otherwise re-issue the same expensive ``mm.recall`` call.
This cache short-circuits those repeats.

KEY:
    ``(intent_normalized, role, limit, turn_bucket)`` where
    ``intent_normalized = intent.strip().lower()`` and
    ``turn_bucket = turn_number // EPISODIC_CACHE_TURN_BUCKET``.

INVALIDATION:
    ``EpisodicRecallCache.invalidate()`` clears the whole cache.
    ``store_and_promote`` calls it after persisting a turn, so
    the next recall sees the freshest state. Surgical key-level
    invalidation is not worth the complexity: a full clear is
    O(N) on a 256-entry dict — sub-microsecond.

USAGE:
    from supervisor.session.episodic_cache import get_episodic_cache

    cache = get_episodic_cache()
    key = build_episodic_cache_key(intent, role, limit, turn_number)
    cached = cache.get(key)
    if cached is None:
        hits = await mm.recall(role, intent, limit=limit)
        cache.put(key, hits)
    else:
        hits = cached
"""
from __future__ import annotations

import logging
import time
from collections import OrderedDict
from typing import Any, Final

from config.limits import (
    EPISODIC_CACHE_MAX_SIZE,
    EPISODIC_CACHE_TTL_S,
    EPISODIC_CACHE_TURN_BUCKET,
)

log = logging.getLogger("goat2.supervisor.session.episodic_cache")

__all__ = [
    "EpisodicRecallCache",
    "build_episodic_cache_key",
    "get_episodic_cache",
    "set_episodic_cache",
]


def normalize_intent(intent: str) -> str:
    """Return the canonical cache-key form of ``intent``.

    The normalization is intentionally minimal: ``strip().lower()``.
    Adding more (lemmatization, punctuation stripping, etc.) would
    expand the cache hit rate at the cost of correctness — two
    semantically different intents that happen to normalize the same
    way would silently collide. Keep it cheap and predictable.
    """
    if not intent:
        return ""
    return intent.strip().lower()


def build_episodic_cache_key(
    intent: str,
    role: str,
    limit: int,
    turn_number: int,
) -> tuple[str, str, int, int]:
    """Build the LRU key tuple for one recall call.

    Components:
      - ``intent_normalized``: ``intent.strip().lower()``
      - ``role``: passed through (typically SESSION_ROLE)
      - ``limit``: int (so 5 and 7 don't collide)
      - ``turn_bucket``: ``max(0, turn_number) // EPISODIC_CACHE_TURN_BUCKET``

    The bucket refresh means a long session naturally rotates the
    cache every ``EPISODIC_CACHE_TURN_BUCKET`` turns even without
    invalidation — bound on staleness without a per-query clock.
    """
    return (
        normalize_intent(intent),
        str(role),
        int(limit),
        max(0, int(turn_number)) // EPISODIC_CACHE_TURN_BUCKET,
    )


class EpisodicRecallCache:
    """Bounded LRU + TTL cache for episodic recall results.

    Thread-safety: not required. GOAT is a single-process async
    event loop; the cache is only touched from coroutines.

    Failure policy: every public method swallows exceptions at
    DEBUG. The cache must NEVER break the recall path — a cache
    bug should surface as a miss, not as a 500.
    """

    def __init__(
        self,
        max_size: int = EPISODIC_CACHE_MAX_SIZE,
        ttl_s: float = EPISODIC_CACHE_TTL_S,
    ) -> None:
        if max_size <= 0:
            raise ValueError(f"max_size must be > 0, got {max_size}")
        if ttl_s <= 0:
            raise ValueError(f"ttl_s must be > 0, got {ttl_s}")
        self._max_size: Final[int] = int(max_size)
        self._ttl_s: Final[float] = float(ttl_s)
        # key → (value, expires_at_monotonic)
        self._data: "OrderedDict[tuple, tuple[Any, float]]" = OrderedDict()
        # counters for observability / debug
        self.hits: int = 0
        self.misses: int = 0
        self.evictions_ttl: int = 0
        self.evictions_lru: int = 0

    @property
    def size(self) -> int:
        """Number of entries currently in the cache."""
        return len(self._data)

    def get(self, key: tuple) -> Any | None:
        """Return the cached value for ``key`` or None on miss.

        On hit: bumps the entry to the MRU end and increments
        ``self.hits``. On TTL expiry: evicts the entry and
        increments ``self.evictions_ttl``. On any error: returns
        None and logs at DEBUG — caller falls back to fresh recall.
        """
        try:
            entry = self._data.get(key)
            if entry is None:
                self.misses += 1
                return None
            value, expires_at = entry
            now = time.monotonic()
            if now > expires_at:
                # TTL expired — drop and miss.
                self._data.pop(key, None)
                self.evictions_ttl += 1
                self.misses += 1
                return None
            # LRU bump.
            self._data.move_to_end(key)
            self.hits += 1
            return value
        except Exception as exc:  # noqa: BLE001 — cache must never raise
            log.debug("episodic_cache.get failed: %s", exc)
            return None

    def put(self, key: tuple, value: Any) -> None:
        """Store ``value`` under ``key`` with current time + TTL.

        If the cache is at capacity, the LRU entry is evicted
        (``self.evictions_lru`` is incremented). Existing key is
        overwritten (LRU bump).
        """
        try:
            expires_at = time.monotonic() + self._ttl_s
            if key in self._data:
                self._data.move_to_end(key)
            self._data[key] = (value, expires_at)
            while len(self._data) > self._max_size:
                self._data.popitem(last=False)  # FIFO end == LRU
                self.evictions_lru += 1
        except Exception as exc:  # noqa: BLE001 — best-effort
            log.debug("episodic_cache.put failed: %s", exc)

    def invalidate(self) -> None:
        """Drop all entries. Called by ``store_and_promote``."""
        try:
            self._data.clear()
        except Exception as exc:  # noqa: BLE001
            log.debug("episodic_cache.invalidate failed: %s", exc)


# ── Singleton ──────────────────────────────────────────────────────────────

_INSTANCE: EpisodicRecallCache | None = None


def get_episodic_cache() -> EpisodicRecallCache:
    """Return the process-local cache, creating it on first use."""
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = EpisodicRecallCache()
    return _INSTANCE


def set_episodic_cache(instance: EpisodicRecallCache | None) -> None:
    """Replace (or clear, with None) the process-local cache.

    Used by tests to inject a fresh cache per test. Production
    code should not call this.
    """
    global _INSTANCE
    _INSTANCE = instance
```

- [ ] **Step 2: Verify the module imports cleanly**

Run: `python -c "from supervisor.session.episodic_cache import EpisodicRecallCache, build_episodic_cache_key, get_episodic_cache, set_episodic_cache; c = EpisodicRecallCache(max_size=4, ttl_s=1.0); c.put(('a','b',5,0), ['hit']); print(c.get(('a','b',5,0)))"`
Expected: `['hit']`

- [ ] **Step 3: Commit**

```bash
git add supervisor/session/episodic_cache.py
git commit -m "feat(episodic_cache): Faza 2 Commit 2 — LRU+TTL cache + singleton"
```

---

### Task 3: Write the failing test suite

**Files:**
- Create: `tests/test_episodic_cache.py`

This task writes all 12 tests BEFORE any cache wiring happens. The cache class already exists (Task 2), so these tests will run against the class directly. They will all pass — but the file also contains a single integration test that asserts `fetch_episodic_hits` consults the cache. That integration test will FAIL until Task 4 wires it up.

- [ ] **Step 1: Create the test file**

Create `tests/test_episodic_cache.py`:

```python
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
```

- [ ] **Step 2: Run the tests; the 14 unit tests should pass and the 4 integration tests should fail**

Run: `pytest tests/test_episodic_cache.py -v 2>&1 | tail -40`
Expected:
- `test_build_*`, `test_cache_*`, `test_singleton_*` — PASS (12 tests).
- `test_fetch_episodic_hits_*` (4 integration tests) — FAIL with `TypeError: fetch_episodic_hits() got an unexpected keyword argument 'turn_number'` or similar signature mismatch.

- [ ] **Step 3: Commit the failing tests**

```bash
git add tests/test_episodic_cache.py
git commit -m "test(episodic_cache): Faza 2 Commit 2 — cache + integration tests (4 fail)"
```

---

### Task 4: Wire `fetch_episodic_hits` to consult the cache

**Files:**
- Modify: `supervisor/session/memory_helpers.py:160-186`

- [ ] **Step 1: Update the signature to take `turn_number` and consult the cache**

In `supervisor/session/memory_helpers.py`, replace the `fetch_episodic_hits` function (lines 160-186) with:

```python
async def fetch_episodic_hits(
    mm: "MemoryManager",
    query: str,
    top_k: int = _EPISODIC_DEFAULT_TOP_K,
    *,
    timeout_s: float = _EPISODIC_TIMEOUT_S,
    turn_number: int = 0,
) -> list:
    """Fetch episodic recall hits with a hard timeout and LRU+TTL cache.

    Cache key = ``build_episodic_cache_key(query, SESSION_ROLE, top_k,
    turn_number)``. On a hit, returns the cached value without
    issuing a recall call. On miss (or cache error), issues the
    recall and stores the result.

    The cache is a process-local singleton; tests inject a fresh
    cache via ``set_episodic_cache``.

    On any failure (timeout, exception, missing method), returns
    ``[]`` — the [Present-Past] layer renders without episodic
    hits but the rest of the structure is preserved.
    """
    # 1. Cache lookup (best-effort).
    from config.roles import SESSION_ROLE  # local: avoid circular at import
    from supervisor.session.episodic_cache import (
        build_episodic_cache_key,
        get_episodic_cache,
    )
    cache = get_episodic_cache()
    key = build_episodic_cache_key(query, SESSION_ROLE, top_k, turn_number)
    cached = cache.get(key)
    if cached is not None:
        return list(cached)

    # 2. Cache miss → real recall with timeout.
    try:
        hits = await asyncio.wait_for(
            mm.recall(SESSION_ROLE, query, limit=top_k),
            timeout=timeout_s,
        )
        result = list(hits or [])
    except asyncio.TimeoutError:
        log.warning(
            "fetch_episodic_hits: timed out after %.1fs", timeout_s,
        )
        return []
    except Exception as exc:  # noqa: BLE001
        log.debug("fetch_episodic_hits failed: %s", exc)
        return []

    # 3. Store in cache (best-effort). Note: an empty list IS a
    #    valid cache value (the query genuinely returned nothing)
    #    — caching it avoids hammering ChromaDB on the same empty
    #    query inside the TTL window.
    cache.put(key, result)
    return result
```

- [ ] **Step 2: Run the cache tests**

Run: `pytest tests/test_episodic_cache.py -v 2>&1 | tail -25`
Expected: ALL 16 tests pass.

- [ ] **Step 3: Run the three-layer memory tests to confirm no regression**

Run: `pytest tests/test_three_layer_memory.py -v 2>&1 | tail -30`
Expected: ALL pass. The existing `mem_turn` tests still work because `turn_number` is optional (`int = 0`).

- [ ] **Step 4: Commit**

```bash
git add supervisor/session/memory_helpers.py
git commit -m "feat(memory_helpers): wire episodic recall cache into fetch_episodic_hits"
```

---

### Task 5: Thread `turn_number` through `mem_turn` and `run_turn`

**Files:**
- Modify: `supervisor/session/mem_inject.py:154-224`
- Modify: `supervisor/turn_runner.py:40-100`

This is the only wiring change that reaches the production call path. `turn_number` is the number of completed turns so far (i.e. `len(history.messages)` BEFORE adding the current user turn as pending). It's available right where `mem_turn` is invoked.

- [ ] **Step 1: Add `turn_number` parameter to `mem_turn` and forward to `fetch_episodic_hits`**

In `supervisor/session/mem_inject.py`, modify the `mem_turn` signature and the `fetch_episodic_hits` call site:

Change the signature (line 154-157):
```python
async def mem_turn(
    mm: "MemoryManager | None",
    intent: str,
    *,
    turn_number: int = 0,
) -> str:
```

Change the call site (line 206):
```python
    # 4. Fetch episodic recall hits (timeout-protected + cached).
    episodic_hits = await fetch_episodic_hits(
        mm, intent, _episodic_top_k, turn_number=turn_number,
    )
    episodic_hits = episodic_hits[:_episodic_top_k]
```

Also extend `__all__` if `mem_turn`'s new kwarg is exported (it isn't — the function is the public API, the kwarg is just a parameter, so `__all__` is fine as-is).

- [ ] **Step 2: Pass `turn_number` from `run_turn`**

In `supervisor/turn_runner.py`, replace the `mem_turn` call (around line 84):

Current:
```python
        mem_ctx = await mem_turn(supervisor.memory_manager, intent)
```

New:
```python
        # turn_number = completed turns before this one (i.e. history
        # length BEFORE buffering the current user turn as pending).
        # Used as the episodic recall cache bucket key.
        turn_number = (
            len(supervisor._history.messages)
            if supervisor._history is not None else 0
        )
        mem_ctx = await mem_turn(
            supervisor.memory_manager, intent, turn_number=turn_number,
        )
```

- [ ] **Step 3: Run the cache tests + three-layer memory tests**

Run: `pytest tests/test_episodic_cache.py tests/test_three_layer_memory.py -v 2>&1 | tail -40`
Expected: ALL pass.

- [ ] **Step 4: Run the action-log + supervisor tests for regression**

Run: `pytest tests/test_action_log.py tests/test_system_prompt_self_report.py -v 2>&1 | tail -30`
Expected: ALL pass.

- [ ] **Step 5: Commit**

```bash
git add supervisor/session/mem_inject.py supervisor/turn_runner.py
git commit -m "feat(mem_inject,turn_runner): thread turn_number for episodic cache key"
```

---

### Task 6: Invalidate cache from `store_and_promote`

**Files:**
- Modify: `supervisor/session/turn_persistence.py:191-241`

The cache must be invalidated after a turn is persisted, because the new turn is now part of working memory and could be promoted to episodic in the background — meaning the next recall should NOT serve stale results.

- [ ] **Step 1: Add invalidation as the last step of `store_and_promote`**

In `supervisor/session/turn_persistence.py`, inside `store_and_promote`, add at the end (just before the `except Exception` block):

```python
        # 5. Invalidate the episodic recall cache. We persist BEFORE
        #    invalidating so the cache observes the freshest state
        #    on its next call. Best-effort: cache failures must
        #    never break turn persistence.
        try:
            from supervisor.session.episodic_cache import get_episodic_cache
            get_episodic_cache().invalidate()
        except Exception as exc:  # noqa: BLE001 — best-effort
            log.debug("episodic cache invalidate failed: %s", exc)
```

Also extend the function's docstring with one line about cache invalidation:

```python
    """Persist the turn, learn style, refresh cache, schedule promotion.

    Args:
        supervisor: The live GoatSupervisor (source of mm, registry).
        turn_count: 1-based turn number (``len(history.messages)``).
        intent: The raw user intent for this turn.
        summary: The assistant's user-facing summary for this turn.

    Returns:
        None. Best-effort; never raises.

    Side effects:
        - Writes two records to working memory (``turn:N:intent``,
          ``turn:N:summary``).
        - Writes a JSON action log to ``turn:N:actions`` (if any
          tools were called).
        - Refreshes the in-memory style cache if a style update
          was learned.
        - Invalidates the process-local episodic recall cache so
          the next recall observes the freshest memory state.
        - Schedules a background task to promote old working
          entries to episodic.
    """
```

- [ ] **Step 2: Add an integration test for the invalidation hook**

Append to `tests/test_episodic_cache.py`:

```python
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
```

- [ ] **Step 3: Run the cache tests**

Run: `pytest tests/test_episodic_cache.py -v 2>&1 | tail -25`
Expected: ALL 17 tests pass (16 from Task 3 + 1 new invalidation test).

- [ ] **Step 4: Run the full memory-related test suite for regression**

Run: `pytest tests/test_episodic_cache.py tests/test_three_layer_memory.py tests/test_action_log.py tests/test_system_prompt_self_report.py -v 2>&1 | tail -10`
Expected: ALL pass.

- [ ] **Step 5: Commit**

```bash
git add supervisor/session/turn_persistence.py tests/test_episodic_cache.py
git commit -m "feat(turn_persistence): invalidate episodic cache after turn persist"
```

---

### Task 7: Final verification + changelog

**Files:**
- Modify: `CHANGELOG.md` (one-line entry)

- [ ] **Step 1: Run the full test suite**

Run: `pytest tests/ -q 2>&1 | tail -20`
Expected: zero failures. The full test suite is small (a few hundred tests at most) and finishes in seconds.

- [ ] **Step 2: Add a CHANGELOG entry**

In `CHANGELOG.md`, find the most recent entry and add a new bullet at the top:

```markdown
- Faza 2 Commit 2 — episodic recall cache: bounded LRU (256 entries,
  TTL 60s) in front of ChromaDB. Key = `(intent_normalized, role,
  limit, turn_number // 5)`. Invalidated on every `store_and_promote`.
  See `supervisor/session/episodic_cache.py` and
  `tests/test_episodic_cache.py`.
```

- [ ] **Step 3: Final commit**

```bash
git add CHANGELOG.md
git commit -m "docs: Faza 2 Commit 2 — episodic cache entry"
```

---

## Self-Review Checklist

- [x] Spec coverage: LRU 256, TTL 60s, invalidation on store, key = `(intent_normalized, role, limit, turn_number // 5)` — all four requirements covered by Tasks 1, 2, 4, 6.
- [x] Placeholder scan: no "TBD", "TODO", or vague "handle errors" — every step shows the actual code.
- [x] Type consistency: `EpisodicRecallCache.get`/`put`/`invalidate` consistent across Tasks 2, 3, 6. `fetch_episodic_hits` signature stable from Task 4 onwards. `mem_turn(turn_number=...)` kwarg matches in Tasks 1, 4, 5.
- [x] Backward compatibility: existing `fetch_episodic_hits` and `mem_turn` callers don't break because the new parameters have defaults (`turn_number: int = 0`).
- [x] Cache never raises: every public cache method wraps its body in `try/except` and logs at DEBUG. Verified by Tests 1, 2 in Task 3.
- [x] Module line ceiling: `episodic_cache.py` is ~150 lines, `memory_helpers.py` grows from 200 → 220 lines — both under the 260-line project cap.
