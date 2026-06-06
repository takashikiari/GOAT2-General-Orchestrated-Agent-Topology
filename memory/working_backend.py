"""StorageBackend Protocol for working-memory storage implementations.

Defines structural interface for DictBackend and RedisBackend.
expires_at is absolute wall-clock timestamp (time.time() + ttl).
"""
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
    DictBackend enforces it on read; RedisBackend passes remaining
    seconds to Redis EXPIRE command.

    Conformance is structural (Protocol), not nominal.
    """

    async def set(
        self,
        ns: AgentRole,
        key: MemoryKey,
        record: RecordDict,
        *,
        expires_at: float | None,
    ) -> None:
        """Store record with optional expiry timestamp."""
        ...

    async def get(
        self, ns: AgentRole, key: MemoryKey
    ) -> RecordDict | None:
        """Retrieve record; None if not found or expired."""
        ...

    async def delete(
        self, ns: AgentRole, key: MemoryKey
    ) -> bool:
        """Delete record; True if existed."""
        ...

    async def keys(self, ns: AgentRole) -> list[MemoryKey]:
        """List all keys in namespace."""
        ...

    async def flush(self, ns: AgentRole) -> int:
        """Clear all records in namespace; returns count."""
        ...

    async def ping(self) -> bool:
        """Health check; True if backend is operational."""
        ...


@dataclass(slots=True)
class _StoredItem:
    """
    One entry in DictBackend with optional expiry.

    expiry is checked lazily on read via is_expired().
    """

    record: RecordDict
    expires_at: float | None

    def is_expired(self) -> bool:
        """Check if item has expired based on wall-clock time."""
        return self.expires_at is not None and time.time() > self.expires_at
