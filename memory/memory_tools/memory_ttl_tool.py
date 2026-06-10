"""Memory TTL tool — view TTL/expiry for working memory entries.

Provides MEMORY_TTL ToolDefinition for supervisor to check
remaining TTL for entries in working memory tier.

MEMORY ACCESS ARCHITECTURE:
===========================
- GOAT (supervisor): Full access to all tiers (checks working only)
- DAG agents: Working tier only

BEHAVIOR:
=========
- Returns remaining TTL in seconds for Redis-backed entries
- None = never expires (no TTL set)
- -1.0 = key not found or already expired
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from agents.base_agent import ToolDefinition
from config.roles import GOAT_ROLE
from memory.memory_tools.memory_helpers import format_memory_error, validate_tier
from tools.registry_accessor import get_registry

if TYPE_CHECKING:
    from memory.memory_manager import MemoryManager

__all__ = ["MEMORY_TTL"]


async def _ttl_handler(
    key: str,
    memory_manager: "MemoryManager | None" = None,
) -> str:
    """Get remaining TTL for a working memory entry.

    MEMORY ACCESS:
    - GOAT supervisor: Full access
    - DAG agents: Working tier only

    Args:
        key: Memory key to check TTL for
        memory_manager: Optional injected MemoryManager

    Returns:
        TTL message with remaining seconds, or error
    """
    if memory_manager is None:
        registry = get_registry()
        memory_manager = registry.memory_manager

    try:
        ttl = await memory_manager.working.ttl_of(GOAT_ROLE, key)
        if ttl is None:
            return f"{key!r}: no expiry (never expires)"
        if ttl < 0:
            return f"{key!r}: not found or expired"
        if ttl == 0:
            return f"{key!r}: expires now (about to expire)"
        # Format TTL nicely
        if ttl >= 3600:
            hours = ttl // 3600
            mins = (ttl % 3600) // 60
            return f"{key!r}: {hours}h {mins}m remaining"
        if ttl >= 60:
            mins = ttl // 60
            secs = ttl % 60
            return f"{key!r}: {mins}m {secs}s remaining"
        return f"{key!r}: {ttl:.1f}s remaining"
    except Exception as exc:
        return format_memory_error("memory_ttl", exc)


MEMORY_TTL = ToolDefinition(
    name="memory_ttl",
    description="Check remaining TTL for a working memory entry.",
    parameters={
        "type": "object",
        "required": ["key"],
        "properties": {
            "key": {
                "type": "string",
                "description": "Memory key to check TTL for.",
            },
        },
    },
    handler=_ttl_handler,
)