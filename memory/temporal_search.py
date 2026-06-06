"""TemporalSearchMixin — Timeline and recent entry queries for MemoryManager.

Provides temporal filtering, timeline queries, and debug tracing across
all memory tiers.
"""
from __future__ import annotations

import logging

from memory.temporal_filter import filter_by_time, resolve_range
from memory.temporal_list import gather_tier_list
from memory.types import MemoryEntry

__all__ = ["TemporalSearchMixin"]

log = logging.getLogger("goat2.memory.temporal")
_TS_KEY = lambda e: float(e.metadata.get("created_at_ts") or 0)  # noqa: E731


class TemporalSearchMixin:
    """
    Timeline, recent, and debug_trace methods for MemoryManager.

    Provides temporal filtering and cross-tier timeline queries.
    All results are sorted by recency (newest first).
    """

    _layers: dict  # MemoryType → MemoryLayer; provided by MemoryManager

    async def timeline(
        self,
        agent_role: str,
        start_datetime: str | None,
        end_datetime: str | None,
        *,
        tier: str = "any",
        limit: int = 100,
    ) -> list[MemoryEntry]:
        """
        Return entries in [start_datetime, end_datetime], newest first.

        Args:
            agent_role: The agent role identifier
            start_datetime: ISO 8601 or natural language start bound
            end_datetime: ISO 8601 or natural language end bound
            tier: Specific tier or "any" for all
            limit: Maximum results to return
        """
        start_ts, end_ts = resolve_range(start_datetime, end_datetime)
        raw = await gather_tier_list(
            self._layers, agent_role, tier, limit=limit * 4
        )
        filtered = sorted(
            filter_by_time(raw, start_ts, end_ts), key=_TS_KEY, reverse=True
        )
        return filtered[:limit]

    async def recent(
        self,
        agent_role: str,
        *,
        limit: int = 50,
        tier: str = "any",
    ) -> list[MemoryEntry]:
        """
        Return the `limit` most recent entries across tier(s).

        Results are sorted newest first. Fetches extra entries to
        compensate for any post-filtering.
        """
        entries = await gather_tier_list(
            self._layers, agent_role, tier, limit=limit * 3
        )
        return sorted(entries, key=_TS_KEY, reverse=True)[:limit]

    async def debug_trace(
        self,
        agent_role: str,
        query: str,
        start_datetime: str | None = None,
        end_datetime: str | None = None,
    ) -> dict:
        """
        Search each tier separately; return per-tier match counts.

        Useful for debugging query routing and understanding which
        tiers contain relevant results. Returns snippets of top matches.
        """
        from memory.memory_enums import MemoryType

        start_ts, end_ts = resolve_range(start_datetime, end_datetime)
        tiers: dict = {}

        for name in ("working", "episodic", "long_term"):
            mt = MemoryType(name)
            try:
                raw = await self._layers[mt].search(agent_role, query, limit=20)
            except Exception as exc:
                log.warning("debug_trace %s error: %s", name, exc)
                raw = []
            matched = filter_by_time(raw, start_ts, end_ts)
            tiers[name] = {
                "total": len(raw),
                "matched": len(matched),
                "entries": [
                    {
                        "key": str(e.key),
                        "created_at": str(e.created_at) or "unknown",
                        "snippet": e.content[:80],
                    }
                    for e in matched[:3]
                ],
            }
        return {
            "query": query,
            "range": (start_datetime, end_datetime),
            "tiers": tiers,
        }
