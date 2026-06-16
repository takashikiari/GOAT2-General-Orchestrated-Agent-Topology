"""Memory count tool — count entries per memory tier.

Provides MEMORY_COUNT ToolDefinition for supervisor to get
the exact number of entries stored in each memory tier.

MEMORY ACCESS ARCHITECTURE:
===========================
- GOAT (supervisor): Full access to all tiers
- DAG agents: Working tier only

OUTPUT:
=======
Returns a summary showing entry count for each tier:
- working: Redis-backed entries with TTL
- episodic: ChromaDB persistent entries
- long_term: Letta core memory blocks
"""
from __future__ import annotations

import logging

log = logging.getLogger("goat2.memory.tools")

from typing import TYPE_CHECKING

from config.roles import GOAT_ROLE, SESSION_ROLE
from memory.memory_tools.memory_helpers import format_memory_error, make_tool

if TYPE_CHECKING:
    from memory.shared.memory_manager import MemoryManager

__all__ = ["MEMORY_COUNT"]


async def _count_handler(
    tier: str = "all",
    memory_manager: "MemoryManager | None" = None,
) -> str:
    """Count entries in a specific tier or all tiers.

    MEMORY ACCESS:
    - GOAT supervisor: Full access to all tiers
    - DAG agents: Working tier only

    Args:
        tier: 'working', 'episodic', 'long_term', or 'all'
        memory_manager: Optional injected MemoryManager

    Returns:
        Count message for specified tier(s)
    """
    if memory_manager is None:
        from tools.registry_accessor import get_registry
        registry = get_registry()
        memory_manager = registry.memory_manager

    valid_tiers = ("working", "episodic", "long_term", "all")
    if tier not in valid_tiers:
        return f"ERROR: invalid tier '{tier}'; valid: {valid_tiers}"

    log.debug("memory_count: tier=%s", tier)
    try:
        counts: dict[str, int] = {}

        if tier in ("working", "all"):
            counts["working"] = await memory_manager.working.count(SESSION_ROLE)

        if tier in ("episodic", "all"):
            counts["episodic"] = await memory_manager.episodic.count(GOAT_ROLE)

        if tier in ("long_term", "all"):
            # Letta doesn't have a simple count - approximate via search
            try:
                results = await memory_manager.long_term.search(
                    GOAT_ROLE, "", limit=1
                )
                counts["long_term"] = getattr(results, 'total', 'unknown')
            except Exception:
                counts["long_term"] = "?"

        if tier == "all":
            lines = [f"Entry count per tier for {GOAT_ROLE}:"]
            for t, c in counts.items():
                lines.append(f"  {t}: {c}")
            return "\n".join(lines)

        return f"{tier}: {counts[tier]} entries"
    except Exception as exc:
        return format_memory_error("memory_count", exc)


MEMORY_COUNT = make_tool(
    name="memory_count",
    description="Count entries in a specific memory tier or all tiers.",
    parameters={
        "type": "object",
        "required": [],
        "properties": {
            "tier": {
                "type": "string",
                "enum": ["working", "episodic", "long_term", "all"],
                "description": "Tier to count (default: 'all').",
                "default": "all",
            },
        },
    },
    handler=_count_handler,
)