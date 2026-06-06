"""MemoryManager — Single entry-point for all agent memory operations.

Agents import only this module for memory access. Coordinates three tiers:
WORKING (session-scoped, TTL), EPISODIC (ChromaDB semantic), LONG_TERM (Letta).
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from memory.chromadb_client import ChromaMemoryClient, chroma_client
from memory.letta_client import LettaClient, letta_client
from memory.memory_crud import MemoryCrudMixin
from memory.memory_enums import LayerStatus, MemoryType
from memory.memory_promote import MemoryPromoteMixin
from memory.memory_search import MemorySearchMixin
from memory.temporal_search import TemporalSearchMixin
from memory.working_memory import WorkingMemoryLayer, working_memory as _default_working

if TYPE_CHECKING:
    from memory.router import MemoryRouter
    from memory.types import AgentRole, MemoryEntry, MemoryLayer

log = logging.getLogger("goat2.memory.manager")

__all__ = ["MemoryManager", "MemoryType", "LayerStatus", "memory_manager"]


class MemoryManager(
    MemoryCrudMixin,
    MemorySearchMixin,
    MemoryPromoteMixin,
    TemporalSearchMixin,
):
    """
    Single entry-point for all agent memory operations.

    Coordinates three memory tiers:
    - WORKING: Session-scoped, TTL-enforced, fastest
    - EPISODIC: ChromaDB semantic search, persistent
    - LONG_TERM: Letta core-memory blocks, most persistent

    Agents should import only this class, not individual layers.
    """

    def __init__(
        self,
        working: WorkingMemoryLayer | None = None,
        episodic: ChromaMemoryClient | None = None,
        long_term: LettaClient | None = None,
    ) -> None:
        self.working: WorkingMemoryLayer = working or _default_working
        self.episodic: ChromaMemoryClient = episodic or chroma_client
        self.long_term: LettaClient = long_term or letta_client
        self._layers: dict[MemoryType, MemoryLayer] = {
            MemoryType.WORKING: self.working,
            MemoryType.EPISODIC: self.episodic,
            MemoryType.LONG_TERM: self.long_term,
        }
        self._router: MemoryRouter | None = None

    def _get_router(self) -> MemoryRouter:
        """Lazily initialise MemoryRouter on first routed recall.

        Avoids circular import at module load time.
        """
        if self._router is None:
            from memory.router import MemoryRouter
            self._router = MemoryRouter(self)
        return self._router

    async def recall(
        self,
        agent_role: str,
        query: str,
        *,
        limit: int = 10,
        tags: list[str] | None = None,
    ) -> list[MemoryEntry]:
        """
        Intelligently route recall through MemoryRouter.

        Falls back to fan-out search when tags are specified (router doesn't
        support tag filtering yet). Uses intelligent routing based on query
        classification and historical layer performance.

        Args:
            agent_role: The agent role identifier
            query: Natural language search query
            limit: Maximum results to return
            tags: Optional tag filters (forces fan-out if present)

        Returns:
            List of MemoryEntry objects, deduplicated and sorted by recency
        """
        if tags is not None:
            return await self._fan_out_search(
                agent_role, query, limit=limit, tags=tags
            )
        from memory.types import AgentRole
        return await self._get_router().search(
            AgentRole(agent_role), query, limit=limit
        )

    async def get_block(self, agent_role: str, label: str) -> str | None:
        """Read a Letta core-memory block for agent_role.

        Core-memory blocks are always-in-context named slots in Letta.
        Returns None if Letta is unreachable or block doesn't exist.
        """
        return await self.long_term.get_block(agent_role, label)

    async def set_block(
        self, agent_role: str, label: str, value: str
    ) -> bool:
        """Write or update a Letta core-memory block.

        Returns False when Letta is unreachable, True on success.
        """
        return await self.long_term.set_block(agent_role, label, value)

    async def status(self) -> LayerStatus:
        """Concurrent health check across all three memory tiers.

        long_term=False is expected when Letta server is not running.
        """
        results = await asyncio.gather(
            self.working.health(),
            self.episodic.health(),
            self.long_term.health(),
            return_exceptions=True,
        )
        return LayerStatus(
            working=results[0] is True,
            episodic=results[1] is True,
            long_term=results[2] is True,
        )

    def __repr__(self) -> str:
        return (
            f"MemoryManager(working={type(self.working).__name__}, "
            f"episodic={type(self.episodic).__name__}, "
            f"long_term={type(self.long_term).__name__})"
        )


memory_manager = MemoryManager()
