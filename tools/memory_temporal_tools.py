"""Temporal memory query tools — timeline, recent, and debug trace.

Provides three ToolDefinition constants (MEMORY_TIMELINE, MEMORY_RECENT,
MEMORY_DEBUG_TRACE) that allow querying the memory manager by time windows,
fetching the most recent entries, or performing a cross-tier debug trace.
"""

from __future__ import annotations

import json

from agents.base_agent import ToolDefinition

__all__ = ["MEMORY_TIMELINE", "MEMORY_RECENT", "MEMORY_DEBUG_TRACE"]

_ROLE = "goat"
_ANY_TIERS = ("any", "working", "episodic", "long_term")


async def _timeline_handler(
    start_datetime: str, end_datetime: str,
    tier: str = "any", limit: int = 100,
) -> str:
    from memory.memory_manager import memory_manager
    try:
        entries = await memory_manager.timeline(
            _ROLE, start_datetime, end_datetime, tier=tier, limit=limit
        )
    except Exception as exc:
        return f"ERROR: memory_timeline failed: {exc}"
    if not entries:
        return f"No entries found between {start_datetime!r} and {end_datetime!r}."
    return "\n".join(
        f"[{e.source}] {e.created_at or 'unknown'} {e.key}: {e.content[:150]}"
        for e in entries
    )


async def _recent_handler(limit: int = 50, tier: str = "any") -> str:
    from memory.memory_manager import memory_manager
    try:
        entries = await memory_manager.recent(_ROLE, limit=limit, tier=tier)
    except Exception as exc:
        return f"ERROR: memory_recent failed: {exc}"
    if not entries:
        return "No recent entries found."
    return "\n".join(
        f"[{e.source}] {e.created_at or 'unknown'} {e.key}: {e.content[:150]}"
        for e in entries
    )


async def _debug_trace_handler(
    query: str,
    start_datetime: str | None = None,
    end_datetime: str | None = None,
) -> str:
    from memory.memory_manager import memory_manager
    try:
        result = await memory_manager.debug_trace(_ROLE, query, start_datetime, end_datetime)
    except Exception as exc:
        return f"ERROR: memory_debug_trace failed: {exc}"
    return json.dumps(result, ensure_ascii=False, indent=2)


MEMORY_TIMELINE = ToolDefinition(
    name="memory_timeline",
    description="Return entries from a specific time window, newest first.",
    parameters={"type": "object", "required": ["start_datetime", "end_datetime"], "properties": {
        "start_datetime": {"type": "string", "description": "ISO 8601 or natural language (e.g. 'yesterday', 'last 7 days')."},
        "end_datetime": {"type": "string", "description": "ISO 8601 or natural-language end bound."},
        "tier": {"type": "string", "enum": list(_ANY_TIERS), "default": "any"},
        "limit": {"type": "integer", "description": "Max results (default 100).", "default": 100},
    }},
    handler=_timeline_handler,
)

MEMORY_RECENT = ToolDefinition(
    name="memory_recent",
    description="Return the N most recent memory entries, newest first.",
    parameters={"type": "object", "required": [], "properties": {
        "limit": {"type": "integer", "description": "Max results (default 50).", "default": 50},
        "tier": {"type": "string", "enum": list(_ANY_TIERS), "default": "any"},
    }},
    handler=_recent_handler,
)

MEMORY_DEBUG_TRACE = ToolDefinition(
    name="memory_debug_trace",
    description="Search each tier separately; show match counts with optional time filter.",
    parameters={"type": "object", "required": ["query"], "properties": {
        "query": {"type": "string", "description": "Semantic search query."},
        "start_datetime": {"type": "string", "description": "Optional ISO 8601 or natural-language start."},
        "end_datetime": {"type": "string", "description": "Optional ISO 8601 or natural-language end."},
    }},
    handler=_debug_trace_handler,
)
