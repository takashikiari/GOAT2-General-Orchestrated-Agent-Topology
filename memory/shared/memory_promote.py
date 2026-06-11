from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from memory.shared.memory_enums import MemoryType

if TYPE_CHECKING:
    from memory.shared.types import MemoryEntry, MemoryLayer

log = logging.getLogger("goat2.memory.shared")


class MemoryPromoteMixin:
    """Inter-tier copy/move operations for MemoryManager."""

    async def promote(
        self, agent_role: str, key: str,
        *, from_type: MemoryType | str = MemoryType.WORKING,
        to_type: MemoryType | str = MemoryType.EPISODIC,
        keep_source: bool = False,
    ) -> MemoryEntry | None:
        """
        Copy one entry between tiers (move semantics by default — source deleted after copy).
        Returns the new MemoryEntry in the destination tier, or None if source not found.
        """
        source = await self.retrieve(agent_role, key, memory_type=from_type)  # type: ignore[attr-defined]
        if source is None:
            log.debug("promote(%s, %s): not found in %s", agent_role, key, from_type)
            return None

        # Skip empty/whitespace content to prevent validation errors
        if not source.content or not source.content.strip():
            log.debug("promote(%s, %s): skipping empty content", agent_role, key)
            return None

        destination = await self.store(  # type: ignore[attr-defined]
            agent_role, key, source.content,
            memory_type=to_type,
            metadata=dict(source.metadata) if source.metadata else None,
        )

        if not keep_source:
            await self.delete(agent_role, key, memory_type=from_type)  # type: ignore[attr-defined]

        log.debug(
            "promote(%s, %s): %s → %s (keep_source=%s)",
            agent_role, key, from_type, to_type, keep_source,
        )
        return destination

    async def promote_all(
        self, agent_role: str,
        *, from_type: MemoryType | str = MemoryType.WORKING,
        to_type: MemoryType | str = MemoryType.EPISODIC,
        keep_source: bool = False,
        limit: int = 200,
    ) -> int:
        """
        Bulk-promote every entry in from_type to to_type.
        `limit` caps the batch to prevent accidentally flushing a huge working-memory dump.
        """
        entries = await self.list(agent_role, memory_type=from_type, limit=limit)  # type: ignore[attr-defined]
        if not entries:
            return 0

        promoted = 0
        for entry in entries:
            result = await self.promote(
                agent_role, entry.key,
                from_type=from_type, to_type=to_type, keep_source=keep_source,
            )
            if result is not None:
                promoted += 1

        log.info(
            "promote_all(%s): %d/%d entries %s → %s",
            agent_role, promoted, len(entries), from_type, to_type,
        )
        return promoted
