"""
memory.session_cache — Session Cache layer (distinct from the L2.5 activation
layer in memory.activation — this one memoizes search/tool results, the other
holds per-chat thread state).

Caches search results and tool outputs within a session. Eliminates
duplicate searches, reduces cost, reduces latency. TTL is configurable
(default 5 minutes = 300 seconds).

The cache reuses the ``WorkingMemory`` Redis client (shared connection
pool) and stores each entry under its own ``cache:{chat_id}:{key}`` key
with a TTL. That namespace never collides with working-memory message
keys (``goat2:working:{chat_id}``), so cache entries and conversation
history coexist cleanly. Values are JSON-serialised dicts.

Transparent to GOAT and the Orchestrator — they call the mapper, which
calls this cache; neither knows a cache is involved.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from utils.logging.setup import get_logger

if TYPE_CHECKING:
    from memory.working import WorkingMemory

log = get_logger(__name__)

# Redis key namespace. Distinct from working-memory's ``goat2:working:``
# prefix so cache entries never collide with conversation history. A
# namespace constant (not a tunable), mirroring working.py's _KEY_PREFIX.
_CACHE_PREFIX = "cache"


class SessionCache:
    """Session Cache layer.

    Caches search results and tool outputs within a session. Eliminates
    duplicate searches, reduces cost, reduces latency. TTL is configurable
    (default 5 minutes = 300 seconds).

    Entries live in Redis under ``cache:{chat_id}:{key}`` with a TTL, reusing
    the working-memory Redis connection. Expired or absent entries are treated
    as misses and return ``None``.
    """

    def __init__(self, working_memory: "WorkingMemory", ttl_seconds: int = 300) -> None:
        """
        Bind the cache to a working-memory Redis backend and a TTL.

        Args:
            working_memory: WorkingMemory instance (Redis backend). Its
                lazily-built Redis client is shared — no second connection
                pool is created.
            ttl_seconds: TTL for cache entries (default 300s = 5min). A
                non-positive value stores entries without expiry.
        """
        self._working = working_memory
        self._ttl = ttl_seconds

    def _cache_key(self, chat_id: str, key: str) -> str:
        """Build the Redis key: ``cache:{chat_id}:{key}``."""
        return f"{_CACHE_PREFIX}:{chat_id}:{key}"

    async def get(self, chat_id: str, key: str) -> dict | None:
        """Retrieve a cached result.

        Returns the stored dict, or ``None`` if the entry is absent, expired,
        or held corrupt JSON (corrupt entries are logged and treated as
        misses — they never crash the caller). Logs DEBUG on hit/miss.
        """
        redis_key = self._cache_key(chat_id, key)
        data = await self._working._get_client().get(redis_key)
        if data is None:
            log.debug("SessionCache MISS chat=%s key=%s", chat_id, key)
            return None
        try:
            value = json.loads(data)
        except json.JSONDecodeError:
            log.warning("SessionCache corrupt at %s, treating as miss", redis_key)
            return None
        if not isinstance(value, dict):
            log.warning("SessionCache non-dict at %s (type=%s), treating as miss",
                        redis_key, type(value).__name__)
            return None
        log.debug("SessionCache HIT chat=%s key=%s", chat_id, key)
        return value

    async def set(self, chat_id: str, key: str, value: dict) -> None:
        """Store ``value`` in the cache under the TTL.

        The dict is JSON-serialised. When ``ttl_seconds`` is positive the entry
        is written with expiry (``SETEX``); otherwise it persists without a
        TTL. Logs INFO.
        """
        redis_key = self._cache_key(chat_id, key)
        payload = json.dumps(value)
        client = self._working._get_client()
        if self._ttl > 0:
            await client.setex(redis_key, self._ttl, payload)
        else:
            await client.set(redis_key, payload)
        log.info("SessionCache SET chat=%s key=%s ttl=%ss", chat_id, key, self._ttl)

    async def invalidate(self, chat_id: str, key: str) -> None:
        """Remove a single cache entry (no-op if it was absent). Logs INFO."""
        removed = await self._working._get_client().delete(self._cache_key(chat_id, key))
        log.info("SessionCache INVALIDATE chat=%s key=%s removed=%s", chat_id, key, removed)

    async def clear(self, chat_id: str) -> None:
        """Clear all cache entries for ``chat_id`` via a SCAN match pattern.

        Uses ``SCAN`` (not ``KEYS``) so a large keyspace does not block Redis.
        Logs INFO with the number of entries removed.
        """
        pattern = self._cache_key(chat_id, "*")
        client = self._working._get_client()
        keys: list[str] = []
        async for found in client.scan_iter(match=pattern, count=100):
            keys.append(found)
        deleted = 0
        if keys:
            deleted = await client.delete(*keys)
        log.info("SessionCache CLEAR chat=%s removed=%s", chat_id, deleted)

    async def exists(self, chat_id: str, key: str) -> bool:
        """Check whether a cache entry exists without retrieving its value.

        Returns ``True`` if a non-expired entry is present. Logs DEBUG.
        """
        present = await self._working._get_client().exists(self._cache_key(chat_id, key))
        log.debug("SessionCache EXISTS chat=%s key=%s present=%s", chat_id, key, bool(present))
        return bool(present)