"""Episodic recall cache — bounded LRU+TTL in front of ChromaDB.

GOAT 2.0 Faza 2 Commit 2. Repeated turns with the same intent
(common during clarification loops, retries, and short task
spans) otherwise re-issue the same expensive ``mm.recall`` call.
This cache short-circuits these repeats.

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
