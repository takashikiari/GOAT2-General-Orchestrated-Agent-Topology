"""Storage-neutral backend interface for working memory.

Working memory stores short-lived, session-scoped records. The *storage* itself
is an implementation detail — anything that satisfies the
``WorkingMemoryBackend`` Protocol can be plugged in (an in-process dictionary, a
networked key-value store, a future embedded engine). The rest of the system
talks only to this interface and never to a concrete backend type.

Any backend implementing the ``WorkingMemoryBackend`` Protocol works: conformance
is structural (``@runtime_checkable``), so a class only needs to provide these
async methods — it does not need to inherit from anything.

No storage-technology names appear in this module by design; the backend is a
detail, not architecture.
"""
from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

log = logging.getLogger("goat2.memory.working.backend_protocol")

__all__ = ["WorkingMemoryBackend"]


@runtime_checkable
class WorkingMemoryBackend(Protocol):
    """Structural interface every working-memory storage backend must satisfy.

    All methods are namespaced by ``agent_role`` so each role has an isolated
    keyspace. ``expires_at`` is an absolute wall-clock Unix timestamp
    (``time.time() + ttl``) or ``None`` for no expiry; backends enforce it
    however suits them (lazily on read, or natively via the store's own TTL).

    Conformance is structural — any object providing these async methods is a
    valid backend, no inheritance required.
    """

    async def get(self, agent_role: str, key: str) -> dict | None:
        """Return the record stored under ``key`` for ``agent_role``, or None.

        Returns None when the key is absent or has expired.
        """
        ...

    async def set(
        self, agent_role: str, key: str, value: dict, expires_at: float | None
    ) -> None:
        """Store ``value`` under ``key`` for ``agent_role``.

        ``expires_at`` is an absolute Unix timestamp, or None for no expiry.
        Overwrites any existing record at the same key.
        """
        ...

    async def delete(self, agent_role: str, key: str) -> bool:
        """Delete ``key`` for ``agent_role``; return True if it existed."""
        ...

    async def keys(self, agent_role: str) -> list[str]:
        """Return all live (non-expired) keys for ``agent_role``."""
        ...

    async def scan(self, agent_role: str, pattern: str) -> list[str]:
        """Return live keys for ``agent_role`` matching a glob-style ``pattern``."""
        ...

    async def flush(self, agent_role: str) -> int:
        """Delete every record for ``agent_role``; return the count removed."""
        ...

    async def ping(self) -> bool:
        """Health check — True when the backend is reachable and operational."""
        ...
