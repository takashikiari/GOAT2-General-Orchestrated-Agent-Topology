from __future__ import annotations

import json
import logging
import time

from memory.redis_conn import RedisConn
from memory.redis_scan import scan_delete, scan_keys
from memory.types import AgentRole, MemoryKey
from memory.working_backend import StorageBackend
from memory.working_record import RecordDict

log = logging.getLogger("goat2.memory.working")


class RedisBackend(RedisConn, StorageBackend):
    """
    Redis backend for WorkingMemoryLayer.  Drop-in for DictBackend.

    TTL is enforced server-side via Redis EXPIRE — no client-side sweep.
    Requires:  pip install redis[hiredis]>=5.0
    """

    async def set(
        self, ns: AgentRole, key: MemoryKey,
        record: RecordDict, *, expires_at: float | None,
    ) -> None:
        r    = await self._get_redis()
        rkey = self._rkey(ns, key)
        if expires_at is not None:
            remaining = max(1, int(expires_at - time.time()))
            await r.set(rkey, json.dumps(record), ex=remaining)  # type: ignore[union-attr]
        else:
            await r.set(rkey, json.dumps(record))                 # type: ignore[union-attr]

    async def get(self, ns: AgentRole, key: MemoryKey) -> RecordDict | None:
        r   = await self._get_redis()
        raw = await r.get(self._rkey(ns, key))  # type: ignore[union-attr]
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            log.warning("RedisBackend: corrupt record at %s", self._rkey(ns, key))
            await r.delete(self._rkey(ns, key))  # type: ignore[union-attr]
            return None

    async def delete(self, ns: AgentRole, key: MemoryKey) -> bool:
        r = await self._get_redis()
        return bool(await r.delete(self._rkey(ns, key)))  # type: ignore[union-attr]

    async def keys(self, ns: AgentRole) -> list[MemoryKey]:
        r = await self._get_redis()
        return await scan_keys(r, self._ns_pattern(ns), self._ns_prefix(ns))

    async def flush(self, ns: AgentRole) -> int:
        r = await self._get_redis()
        return await scan_delete(r, self._ns_pattern(ns))

    async def ping(self) -> bool:
        try:
            r = await self._get_redis()
            return await r.ping()  # type: ignore[union-attr]
        except Exception as exc:
            log.warning("RedisBackend.ping failed: %s", exc)
            return False
