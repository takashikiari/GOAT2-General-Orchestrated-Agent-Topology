"""LettaClient — Persistent memory via the Letta REST API.

Provides LONG_TERM memory tier with in-context fallback when Letta
server is unreachable. All operations are async with health probing.
"""
from __future__ import annotations

from memory.letta_blocks import do_get_block, do_set_block
from memory.letta_fallback import _InContextFallback
from memory.letta_health import LettaHealthProbe
from memory.letta_ops_list import do_clear, do_list
from memory.letta_ops_retrieve import do_retrieve, do_search
from memory.letta_ops_store import do_delete, do_store
from memory.letta_registry import LettaAgentRegistry
from memory.types import (
    AgentRole,
    MemoryEntry,
    MemoryEntryMetadata,
    MemoryKey,
    MemoryLayer,
)

__all__ = ["LettaClient", "letta_client"]


class LettaClient(MemoryLayer):
    """
    Persistent memory via the Letta REST API with in-context fallback.

    When Letta server is unreachable, falls back to ephemeral in-memory
    storage (_InContextFallback). Health probing determines availability.

    Implements MemoryLayer Protocol for integration with MemoryManager.
    """

    __slots__ = ("_probe", "_registry", "_fallback")

    def __init__(self) -> None:
        self._probe: LettaHealthProbe = LettaHealthProbe()
        self._registry: LettaAgentRegistry = LettaAgentRegistry(self._probe)
        self._fallback: _InContextFallback = _InContextFallback()

    async def store(
        self,
        agent_role: AgentRole,
        key: MemoryKey,
        content: str,
        *,
        metadata: MemoryEntryMetadata | None = None,
        ttl: int | None = None,
    ) -> MemoryEntry:
        """Persist content; falls back to in-memory store if Letta unavailable."""
        meta = metadata or MemoryEntryMetadata(tags=[])
        user_tags = list(meta.get("tags") or [])
        if not await self._probe.is_available():
            return self._fallback.store(agent_role, key, content, meta)
        return await do_store(
            self._probe,
            self._registry,
            self._fallback,
            agent_role,
            key,
            content,
            meta,
            user_tags,
        )

    async def retrieve(
        self, agent_role: AgentRole, key: MemoryKey
    ) -> MemoryEntry | None:
        """Retrieve by key; falls back if Letta unavailable."""
        if not await self._probe.is_available():
            return self._fallback.retrieve(agent_role, key)
        return await do_retrieve(
            self._probe, self._registry, self._fallback, agent_role, key
        )

    async def search(
        self,
        agent_role: AgentRole,
        query: str,
        *,
        limit: int = 5,
        tags: list[str] | None = None,
    ) -> list[MemoryEntry]:
        """Semantic search; falls back if Letta unavailable."""
        if not await self._probe.is_available():
            return self._fallback.search(agent_role, query, limit, tags)
        return await do_search(
            self._probe,
            self._registry,
            self._fallback,
            agent_role,
            query,
            limit,
            tags,
        )

    async def delete(
        self, agent_role: AgentRole, key: MemoryKey
    ) -> bool:
        """Delete by key; falls back if Letta unavailable."""
        if not await self._probe.is_available():
            return self._fallback.delete(agent_role, key)
        return await do_delete(
            self._probe, self._registry, self._fallback, agent_role, key
        )

    async def list(
        self, agent_role: AgentRole, *, limit: int = 20
    ) -> list[MemoryEntry]:
        """List entries; falls back if Letta unavailable."""
        if not await self._probe.is_available():
            return self._fallback.list(agent_role, limit)
        return await do_list(
            self._probe, self._registry, self._fallback, agent_role, limit
        )

    async def clear(self, agent_role: AgentRole) -> int:
        """Clear all entries; falls back if Letta unavailable."""
        if not await self._probe.is_available():
            return self._fallback.clear(agent_role)
        return await do_clear(
            self._probe, self._registry, self._fallback, agent_role
        )

    async def health(self) -> bool:
        """Health check with forced probe."""
        return await self._probe.check(force=True)

    async def get_block(
        self, agent_role: AgentRole, label: str
    ) -> str | None:
        """Read a Letta core-memory block."""
        if not await self._probe.is_available():
            return None
        return await do_get_block(
            self._probe, self._registry, agent_role, label
        )

    async def set_block(
        self, agent_role: AgentRole, label: str, value: str
    ) -> bool:
        """Write a Letta core-memory block."""
        if not await self._probe.is_available():
            return False
        return await do_set_block(
            self._probe, self._registry, agent_role, label, value
        )

    async def close(self) -> None:
        """Close HTTP client connections."""
        await self._probe.close()

    async def __aenter__(self) -> "LettaClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()


letta_client = LettaClient()
