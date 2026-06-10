from __future__ import annotations

import logging

from memory.shared.types import AgentRole, MemoryKey
from memory.working.working_backend import StorageBackend, _StoredItem
from memory.working.working_record import RecordDict

log = logging.getLogger("goat2.memory.working")


class DictBackend(StorageBackend):
    """
    In-process Python dict backend.  Zero dependencies.

    TTL is enforced lazily on read.  Call sweep() or schedule via
    WorkingMemoryLayer.start_sweep_task() for proactive eviction.
    """

    __slots__ = ("_store",)

    def __init__(self) -> None:
        self._store: dict[AgentRole, dict[MemoryKey, _StoredItem]] = {}

    async def set(
        self, ns: AgentRole, key: MemoryKey,
        record: RecordDict, *, expires_at: float | None,
    ) -> None:
        bucket = self._store.setdefault(ns, {})
        bucket.pop(key, None)
        bucket[key] = _StoredItem(record=record, expires_at=expires_at)

    async def get(self, ns: AgentRole, key: MemoryKey) -> RecordDict | None:
        item = self._store.get(ns, {}).get(key)
        if item is None:
            return None
        if item.is_expired():
            del self._store[ns][key]
            return None
        return item.record

    async def delete(self, ns: AgentRole, key: MemoryKey) -> bool:
        bucket = self._store.get(ns, {})
        item   = bucket.get(key)
        if item is None:
            return False
        if item.is_expired():
            del bucket[key]
            return False
        del bucket[key]
        return True

    async def keys(self, ns: AgentRole) -> list[MemoryKey]:
        bucket  = self._store.get(ns, {})
        expired = [k for k, v in bucket.items() if v.is_expired()]
        for k in expired:
            del bucket[k]
        return list(bucket.keys())

    async def flush(self, ns: AgentRole) -> int:
        count           = len(self._store.get(ns, {}))
        self._store[ns] = {}
        return count

    async def ping(self) -> bool:
        return True

    def sweep(self) -> int:
        """Evict all expired items across all namespaces."""
        removed = 0
        for bucket in self._store.values():
            expired = [k for k, v in bucket.items() if v.is_expired()]
            for k in expired:
                del bucket[k]
            removed += len(expired)
        if removed:
            log.debug("DictBackend.sweep: evicted %d expired items", removed)
        return removed

    def size(self) -> int:
        return sum(
            1 for bucket in self._store.values()
            for item in bucket.values()
            if not item.is_expired()
        )
