from __future__ import annotations

import asyncio
import logging

from memory.types import MemoryEntry

__all__ = ["gather_tier_list"]

log = logging.getLogger("goat2.memory.temporal")


async def gather_tier_list(
    layers: dict, agent_role: str, tier: str, *, limit: int,
) -> list[MemoryEntry]:
    """List entries from one tier or all three with deduplication by (role, key)."""
    from memory.memory_enums import MemoryType
    if tier != "any":
        try:
            return await layers[MemoryType(tier)].list(agent_role, limit=limit)
        except Exception as exc:
            log.warning("gather_tier_list(%s) error: %s", tier, exc)
            return []
    tasks = [
        layers[MemoryType(t)].list(agent_role, limit=limit)
        for t in ("working", "episodic", "long_term")
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    merged: list[MemoryEntry] = []
    seen: set[str] = set()
    for res in results:
        if isinstance(res, Exception):
            log.warning("gather_tier_list fan-out error: %s", res)
            continue
        for e in res:
            k = f"{e.agent_role}::{e.key}"
            if k not in seen:
                seen.add(k)
                merged.append(e)
    return merged
