"""Redis backend for working memory — implements the ``WorkingMemoryBackend`` protocol.

TTL is enforced server-side via Redis EXPIRE — no client-side sweep needed.
Drop-in replacement for ``DictBackend``. Requires: ``pip install redis[hiredis]>=5.0``.
"""
from __future__ import annotations

import json
import logging
import time

from memory.working.redis_conn import RedisConn
from memory.working.redis_scan import scan_delete, scan_keys

log = logging.getLogger("goat2.memory.working.redis_backend")

__all__ = ["RedisBackend"]


class RedisBackend(RedisConn):
    """Networked key-value backend satisfying ``WorkingMemoryBackend``.

    Conformance is structural — the ``WorkingMemoryBackend`` Protocol is
    satisfied without explicit inheritance.
    """

    async def set(
        self,
        agent_role: str,
        key: str,
        value: dict,
        expires_at: float | None,
    ) -> None:
        """Store ``value`` under ``key`` for ``agent_role`` with optional TTL.

        ``expires_at`` is an absolute Unix timestamp; we convert it to the
        remaining seconds for Redis EXPIRE.
        """
        r    = await self._get_redis()
        rkey = self._rkey(agent_role, key)
        if expires_at is not None:
            remaining = max(1, int(expires_at - time.time()))
            await r.set(rkey, json.dumps(value), ex=remaining)  # type: ignore[union-attr]
        else:
            await r.set(rkey, json.dumps(value))                 # type: ignore[union-attr]
        log.debug("RedisBackend.set(%s, %s) expires_at=%s", agent_role, key, expires_at)

    async def get(self, agent_role: str, key: str) -> dict | None:
        """Return live record or None when absent / corrupt (deletes corrupt)."""
        r   = await self._get_redis()
        raw = await r.get(self._rkey(agent_role, key))  # type: ignore[union-attr]
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            log.warning("RedisBackend: corrupt record at %s", self._rkey(agent_role, key))
            await r.delete(self._rkey(agent_role, key))  # type: ignore[union-attr]
            return None

    async def delete(self, agent_role: str, key: str) -> bool:
        """Delete ``key`` for ``agent_role``; True if it existed."""
        r = await self._get_redis()
        return bool(await r.delete(self._rkey(agent_role, key)))  # type: ignore[union-attr]

    async def keys(self, agent_role: str) -> list[str]:
        """Return all live keys for ``agent_role`` (Redis-native TTL handles expiry)."""
        r = await self._get_redis()
        return await scan_keys(r, self._ns_pattern(agent_role), self._ns_prefix(agent_role))

    async def scan(self, agent_role: str, pattern: str) -> list[str]:
        """Scan Redis keys for ``agent_role`` matching glob ``pattern``."""
        r            = await self._get_redis()
        prefix       = self._ns_prefix(agent_role)
        full_pattern = f"{prefix}{pattern}"
        return await scan_keys(r, full_pattern, prefix)

    async def flush(self, agent_role: str) -> int:
        """Delete every record for ``agent_role``; return the count removed."""
        r = await self._get_redis()
        return await scan_delete(r, self._ns_pattern(agent_role))

    async def ping(self) -> bool:
        """Health check — True when Redis is reachable."""
        try:
            r = await self._get_redis()
            return await r.ping()  # type: ignore[union-attr]
        except Exception as exc:
            log.warning("RedisBackend.ping failed: %s", exc)
            return False
