from __future__ import annotations

import asyncio
import logging

from memory.memory_enums import MemoryType
from memory.temporal_filter import filter_by_time, resolve_range
from memory.types import MemoryEntry, MemoryLayer

log = logging.getLogger("goat2.memory.manager")


class MemorySearchMixin:
    """Single-tier and fan-out search for MemoryManager."""

    _layers: dict[MemoryType, MemoryLayer]

    async def search(
        self, agent_role: str, query: str,
        *, memory_type: MemoryType | str | None = None,
        limit: int = 5, tags: list[str] | None = None,
        start_datetime: str | None = None, end_datetime: str | None = None,
    ) -> list[MemoryEntry]:
        """
        Search one tier (when memory_type is set) or all three concurrently (memory_type=None).
        Fan-out deduplicates by (role, key); WORKING copy wins on duplicate keys.
        start_datetime / end_datetime accept ISO 8601 or natural language (see time_parser).
        """
        start_ts, end_ts = resolve_range(start_datetime, end_datetime)
        has_filter = start_ts is not None or end_ts is not None
        if memory_type is not None:
            fetch = limit * 4 if has_filter else limit
            raw = await self._layers[MemoryType(memory_type)].search(
                agent_role, query, limit=fetch, tags=tags
            )
            return filter_by_time(raw, start_ts, end_ts)[:limit]
        return await self._fan_out_search(
            agent_role, query, limit=limit, tags=tags,
            start_ts=start_ts, end_ts=end_ts,
        )

    async def _fan_out_search(
        self, agent_role: str, query: str,
        *, limit: int, tags: list[str] | None,
        start_ts: float | None = None, end_ts: float | None = None,
    ) -> list[MemoryEntry]:
        fetch = limit * 4 if (start_ts is not None or end_ts is not None) else limit
        order = MemoryType.priority_order()
        tasks = [
            self._layers[mt].search(agent_role, query, limit=fetch, tags=tags)
            for mt in order
        ]
        raw = await asyncio.gather(*tasks, return_exceptions=True)

        merged: list[MemoryEntry] = []
        seen:   set[str]          = set()

        for mt, result in zip(order, raw):
            if isinstance(result, Exception):
                log.warning("fan-out search error in %s: %s", mt.value, result)
                continue
            for entry in filter_by_time(result, start_ts, end_ts):
                dedup_key = f"{entry.agent_role}::{entry.key}"
                if dedup_key not in seen:
                    seen.add(dedup_key)
                    merged.append(entry)

        merged.sort(
            key=lambda e: float(e.metadata.get("created_at_ts", 0) or 0),
            reverse=True,
        )
        return merged[:limit]

    async def recall(
        self, agent_role: str, query: str,
        *, limit: int = 10, tags: list[str] | None = None,
    ) -> list[MemoryEntry]:
        """Named alias for search(memory_type=None) — searches all three tiers concurrently."""
        return await self.search(
            agent_role, query, memory_type=None, limit=limit, tags=tags
        )
