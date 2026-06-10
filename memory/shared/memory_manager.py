"""MemoryManager — Single entry-point for all agent memory operations.

Agents import only this module for memory access. Coordinates three tiers:
WORKING (session-scoped, TTL), EPISODIC (ChromaDB semantic), LONG_TERM (Letta).

PHASE 4 UPDATE:
===============
Module-level `memory_manager = MemoryManager()` singleton REMOVED.
All code must now use Registry for memory manager access.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from memory.episodic.chromadb_client import ChromaMemoryClient
from memory.long_term.letta_client import LettaClient
from memory.shared.memory_crud import MemoryCrudMixin
from memory.shared.memory_enums import LayerStatus, MemoryType
from memory.shared.memory_promote import MemoryPromoteMixin
from memory.shared.memory_search import MemorySearchMixin
from memory.temporal.temporal_search import TemporalSearchMixin
from memory.working.working_memory import WorkingMemoryLayer

if TYPE_CHECKING:
    from memory.router import MemoryRouter
    from memory.shared.types import AgentRole, MemoryEntry, MemoryLayer

log = logging.getLogger("goat2.memory.manager")

__all__ = ["MemoryManager", "MemoryType", "LayerStatus"]


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
        from memory.working.working_memory import WorkingMemoryLayer
        from memory.working.redis_backend import RedisBackend
        
        self.working: WorkingMemoryLayer = working or WorkingMemoryLayer(backend=RedisBackend())
        self.episodic: ChromaMemoryClient = episodic or ChromaMemoryClient()
        self.long_term: LettaClient = long_term or LettaClient()
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
        from memory.shared.types import AgentRole
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

    async def promote_with_guard(
        self,
        agent_role: str,
        key: str,
        *,
        from_type: MemoryType | str = MemoryType.WORKING,
        to_type: MemoryType | str = MemoryType.EPISODIC,
        keep_source: bool = True,
    ) -> bool:
        """Promote entry between tiers with duplicate and quality checks.

        Checks for duplicate in destination tier before promoting.
        Runs PollutionGuard on content to prevent garbage accumulation.
        Skips promotion if checks fail (logged at DEBUG level).

        Args:
            agent_role: Role namespace (e.g., 'user_session')
            key: Memory key to promote
            from_type: Source memory tier
            to_type: Destination memory tier
            keep_source: Whether to retain source entry after promotion

        Returns:
            True if promoted successfully, False if skipped
        """
        from memory.shared.pollution_guard import PollutionGuard

        # Check for duplicate in destination tier
        existing = await self.locate(agent_role, key, memory_type=to_type)
        if existing:
            log.debug("promote_with_guard: skip %s → %s (duplicate exists)", key, to_type)
            return False

        # Get source entry for content validation
        source = await self.locate(agent_role, key, memory_type=from_type)
        if not source:
            log.debug("promote_with_guard: skip %s (source not found)", key)
            return False

        # Run PollutionGuard on content
        guard = PollutionGuard()
        # Extract key-value pairs from content for validation
        content_lines = source.content.splitlines()
        for line in content_lines:
            if ":" in line:
                k = line.partition(":")[0].strip()
                v = line.partition(":")[2].strip()
                result = guard.validate(k, v, "explicit", "")
                if result["decision"] == "blocked":
                    log.debug("promote_with_guard: skip %s (blocked by PollutionGuard)", key)
                    return False

        # All checks passed — perform promotion
        try:
            await self.promote(
                agent_role, key,
                from_type=from_type,
                to_type=to_type,
                keep_source=keep_source,
            )
            log.debug("promote_with_guard: %s %s → %s (keep=%s)", key, from_type, to_type, keep_source)
            return True
        except Exception as e:
            log.warning("promote_with_guard: failed %s → %s: %s", key, from_type, to_type, e)
            return False

    async def promote_turns(
        self,
        agent_role: str,
        turn_count: int,
    ) -> None:
        """Background promotion task for conversation turns based on turn count.

        Promotion rules:
        - Turn 2+ (messages >= 4): WORKING → EPISODIC, keep_source=True
        - Turn 3+ (messages >= 6): EPISODIC → LONG_TERM, keep_source=False

        Runs as non-blocking background task after store_turn().

        Args:
            agent_role: Role namespace (e.g., 'user_session')
            turn_count: Current number of messages in history
        """
        try:
            # Turn 2+ : promote working → episodic
            if turn_count >= 4:
                keys = await self.working.backend.keys(agent_role)
                for key in keys:
                    if key.startswith("turn_"):
                        await self.promote_with_guard(
                            agent_role, key,
                            from_type=MemoryType.WORKING,
                            to_type=MemoryType.EPISODIC,
                            keep_source=True,
                        )
                log.debug("promote_turns: working → episodic for %d keys", len(keys))

            # Turn 3+ : promote episodic → long_term
            if turn_count >= 6:
                # ChromaDB doesn't have simple keys() — use recent entries
                entries = await self.episodic.search(agent_role, "turn", limit=10)
                for entry in entries:
                    key = entry.key if hasattr(entry, 'key') else entry.get('id', '')
                    if key.startswith("turn_"):
                        await self.promote_with_guard(
                            agent_role, key,
                            from_type=MemoryType.EPISODIC,
                            to_type=MemoryType.LONG_TERM,
                            keep_source=False,
                        )
                log.debug("promote_turns: episodic → long_term completed")
        except Exception as e:
            log.warning("promote_turns: background task failed: %s", e)

    def __repr__(self) -> str:
        return (
            f"MemoryManager(working={type(self.working).__name__}, "
            f"episodic={type(self.episodic).__name__}, "
            f"long_term={type(self.long_term).__name__})"
        )
