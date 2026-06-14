from __future__ import annotations

import asyncio
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
        If to_type is LONG_TERM and Letta is unavailable, returns 0 immediately.
        """
        # Guard: probe Letta before starting a potentially large batch
        if to_type in (MemoryType.LONG_TERM, "long_term"):
            long_term = getattr(self, "long_term", None)
            if long_term is not None:
                try:
                    available = await asyncio.wait_for(long_term.health(), timeout=5.0)
                    if not available:
                        log.warning(
                            "promote_all(%s): Letta unavailable — skipping %s → %s",
                            agent_role, from_type, to_type,
                        )
                        return 0
                except (asyncio.TimeoutError, Exception) as exc:
                    log.warning(
                        "promote_all(%s): Letta health check failed (%s) — skipping",
                        agent_role, exc,
                    )
                    return 0
            log.debug("promote_all(%s): Letta available, proceeding", agent_role)

        entries = await self.list(agent_role, memory_type=from_type, limit=limit)  # type: ignore[attr-defined]
        if not entries:
            return 0

        promoted = 0
        for entry in entries:
            try:
                result = await asyncio.wait_for(
                    self.promote(
                        agent_role, entry.key,
                        from_type=from_type, to_type=to_type, keep_source=keep_source,
                    ),
                    timeout=5.0,
                )
                if result is not None:
                    promoted += 1
            except asyncio.TimeoutError:
                log.warning(
                    "promote_all(%s): promote('%s') timed out — stopping batch early",
                    agent_role, entry.key,
                )
                break
            except Exception as exc:
                log.debug("promote_all(%s): promote('%s') failed: %s", agent_role, entry.key, exc)

        log.info(
            "promote_all(%s): %d/%d entries %s → %s",
            agent_role, promoted, len(entries), from_type, to_type,
        )
        return promoted
