"""Storage-neutral backend interface for episodic memory.

Episodic memory is GOAT's medium-term tier — persistent, semantically searchable,
organized into compartments, and bounded by a sliding window (not TTL). The
*storage* itself is an implementation detail: anything satisfying the
``EpisodicMemoryBackend`` Protocol can be plugged in. The rest of the system talks
to this interface, never to a concrete episodic backend type.

Any backend implementing this Protocol works: conformance is structural
(``@runtime_checkable``), so a class only needs to provide these async methods —
no inheritance required. No storage-technology names appear here by design.
"""
from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

log = logging.getLogger("goat2.memory.episodic.backend_protocol")

__all__ = ["EpisodicMemoryBackend"]


@runtime_checkable
class EpisodicMemoryBackend(Protocol):
    """Structural interface every episodic-memory backend must satisfy.

    All operations are namespaced by ``agent_role`` so each role has an isolated
    keyspace. Entries persist (no TTL); capacity is managed by the sliding window.
    Conformance is structural — any object providing these async methods is a
    valid episodic backend.
    """

    async def store(self, agent_role: str, key: str, content: str, metadata: dict) -> object:
        """Persist ``content`` under ``key`` for ``agent_role`` with ``metadata``."""
        ...

    async def get(self, agent_role: str, key: str) -> object | None:
        """Return the entry stored under ``key`` for ``agent_role``, or None."""
        ...

    async def search(self, query: str, limit: int, agent_role: str) -> list:
        """Return up to ``limit`` entries semantically matching ``query``."""
        ...

    async def list(self, agent_role: str, limit: int) -> list:
        """Return up to ``limit`` entries for ``agent_role`` (most recent first)."""
        ...

    async def delete(self, agent_role: str, key: str) -> bool:
        """Delete ``key`` for ``agent_role``; return True if it existed."""
        ...

    async def count(self, agent_role: str) -> int:
        """Return the total number of entries stored for ``agent_role``."""
        ...
