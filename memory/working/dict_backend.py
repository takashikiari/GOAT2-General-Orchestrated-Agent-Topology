"""In-process Python dict backend for working memory.

Implements the ``WorkingMemoryBackend`` protocol (see
``memory/working/backend_protocol.py``). Zero external dependencies, zero
singleton state — instantiate one per backend owner. TTL is enforced lazily
on read; call ``sweep()`` or schedule ``WorkingMemoryLayer.start_sweep_task()``
for proactive eviction.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from memory.working.backend_protocol import WorkingMemoryBackend

log = logging.getLogger("goat2.memory.working.dict_backend")

__all__ = ["DictBackend", "_StoredItem"]


@dataclass(slots=True)
class _StoredItem:
    """One entry in DictBackend with optional expiry.

    ``expires_at`` is an absolute wall-clock timestamp (``time.time() + ttl``)
    or ``None`` for no expiry. Expiry is checked lazily on read via
    ``is_expired()``.
    """

    record: dict
    expires_at: float | None

    def is_expired(self) -> bool:
        """Return True when ``expires_at`` is set and has passed."""
        return self.expires_at is not None and time.time() > self.expires_at


class DictBackend:
    """In-process dict backend satisfying ``WorkingMemoryBackend``.

    Stores records in a nested ``{namespace: {key: _StoredItem}}`` mapping.
    Pure async API; conforms structurally to the Protocol (no inheritance).
    """

    __slots__ = ("_store",)

    def __init__(self) -> None:
        self._store: dict[str, dict[str, _StoredItem]] = {}

    async def set(
        self,
        agent_role: str,
        key: str,
        value: dict,
        expires_at: float | None,
    ) -> None:
        """Store ``value`` under ``key`` for ``agent_role`` (overwrite on dup)."""
        bucket = self._store.setdefault(agent_role, {})
        bucket.pop(key, None)
        bucket[key] = _StoredItem(record=value, expires_at=expires_at)
        log.debug("DictBackend.set(%s, %s) expires_at=%s", agent_role, key, expires_at)

    async def get(self, agent_role: str, key: str) -> dict | None:
        """Return live record or None when absent / expired (evicts on miss)."""
        item = self._store.get(agent_role, {}).get(key)
        if item is None:
            return None
        if item.is_expired():
            del self._store[agent_role][key]
            return None
        return item.record

    async def delete(self, agent_role: str, key: str) -> bool:
        """Delete ``key``; True if it existed and was live (False on miss/expiry)."""
        bucket = self._store.get(agent_role, {})
        item   = bucket.get(key)
        if item is None:
            return False
        if item.is_expired():
            del bucket[key]
            return False
        del bucket[key]
        return True

    async def keys(self, agent_role: str) -> list[str]:
        """Return all live keys for ``agent_role`` (evicts expired as a side effect)."""
        bucket  = self._store.get(agent_role, {})
        expired = [k for k, v in bucket.items() if v.is_expired()]
        for k in expired:
            del bucket[k]
        return list(bucket.keys())

    async def scan(self, agent_role: str, pattern: str) -> list[str]:
        """Return live keys for ``agent_role`` matching glob ``pattern``.

        Mirrors the networked backend's scan via ``fnmatch`` so both satisfy
        the ``WorkingMemoryBackend`` Protocol. No regex.
        """
        from fnmatch import fnmatchcase
        live = await self.keys(agent_role)
        return [k for k in live if fnmatchcase(str(k), pattern)]

    async def flush(self, agent_role: str) -> int:
        """Delete every record for ``agent_role``; return the count removed."""
        count           = len(self._store.get(agent_role, {}))
        self._store[agent_role] = {}
        log.debug("DictBackend.flush(%s): removed %d", agent_role, count)
        return count

    async def ping(self) -> bool:
        """Health check — always True (in-process)."""
        return True

    def sweep(self) -> int:
        """Evict all expired items across all namespaces. Sync utility."""
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
        """Return total live record count across all namespaces."""
        return sum(
            1 for bucket in self._store.values()
            for item in bucket.values()
            if not item.is_expired()
        )
