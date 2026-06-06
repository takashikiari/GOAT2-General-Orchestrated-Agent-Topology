from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from memory.types import AgentRole, MemoryKey
from memory.working_record import RecordDict

__all__ = ["StorageBackend", "_StoredItem"]


@runtime_checkable
class StorageBackend(Protocol):
    """
    Structural interface for working-memory storage backends.

    expires_at is an absolute wall-clock timestamp (time.time() + ttl).
    DictBackend enforces it on read; RedisBackend passes the remaining
    seconds to Redis EXPIRE.
    """

    async def set(
        self, ns: AgentRole, key: MemoryKey,
        record: RecordDict, *, expires_at: float | None,
    ) -> None: ...

    async def get(
        self, ns: AgentRole, key: MemoryKey,
    ) -> RecordDict | None: ...

    async def delete(self, ns: AgentRole, key: MemoryKey) -> bool: ...

    async def keys(self, ns: AgentRole) -> list[MemoryKey]: ...

    async def flush(self, ns: AgentRole) -> int: ...

    async def ping(self) -> bool: ...


@dataclass(slots=True)
class _StoredItem:
    """One entry in DictBackend with optional expiry."""

    record:     RecordDict
    expires_at: float | None

    def is_expired(self) -> bool:
        return self.expires_at is not None and time.time() > self.expires_at
