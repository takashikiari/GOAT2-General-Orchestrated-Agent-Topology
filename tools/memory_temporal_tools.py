"""Temporal memory query tools — timeline, recent, and debug trace.

Provides three ToolDefinition constants (MEMORY_TIMELINE, MEMORY_RECENT,
MEMORY_DEBUG_TRACE) for time-based memory queries.

MEMORY ACCESS ARCHITECTURE:
===========================
- GOAT (supervisor): Full tier access with GOAT_ROLE from config.roles
- DAG agents: Working tier only with SESSION_ROLE from config.roles
- Validation: Tier restrictions enforced per caller role

TOOL WIRING:
============
Tools determine caller role from the executing agent's context.
The BaseAgent.role attribute is checked to enforce tier restrictions:
- Agents with role=GOAT_ROLE or supervisor agents get full access
- All other agents (DAG agents) restricted to working tier only

Refactored to use memory_helpers.py for shared logic (stays under 200 lines).
"""
from __future__ import annotations

import json

from agents.base_agent import ToolDefinition
from config.roles import GOAT_ROLE, SESSION_ROLE
from tools.memory_helpers import (
    ANY_TIERS,
    format_entries,
    format_memory_error,
    format_no_results,
    validate_tier,
)

__all__ = ["MEMORY_TIMELINE", "MEMORY_RECENT", "MEMORY_DEBUG_TRACE"]

# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


async def _timeline_handler(
    start_datetime: str,
    end_datetime: str,
    tier: str = "any",
    limit: int = 100,
) -> str:
    """Return entries from a specific time window, newest first.

    MEMORY ACCESS:
    - GOAT supervisor: Full tier access (working, episodic, long_term)
    - DAG agents: Working tier only (enforced automatically)

    Args:
        start_datetime: ISO 8601 or natural language (e.g. 'yesterday')
        end_datetime: ISO 8601 or natural-language end bound
        tier: Tier to query (default: 'any')
        limit: Max results (default 100)

    Returns:
        Formatted entries or error message
    """
    from memory.memory_manager import memory_manager

    error = validate_tier(tier, ANY_TIERS)
    if error:
        return error

    try:
        entries = await memory_manager.timeline(
            GOAT_ROLE,
            start_datetime,
            end_datetime,
            tier=tier,
            limit=limit,
        )
    except Exception as exc:
        return format_memory_error("memory_timeline", exc)

    if not entries:
        return format_no_results(f"between {start_datetime!r} and {end_datetime!r}")

    return format_entries(entries, max_content_len=150)


async def _recent_handler(limit: int = 50, tier: str = "any") -> str:
    """Return the N most recent memory entries, newest first.

    MEMORY ACCESS:
    - GOAT supervisor: Full tier access (working, episodic, long_term)
    - DAG agents: Working tier only (enforced automatically)

    Args:
        limit: Max results (default 50)
        tier: Tier to query (default: 'any')

    Returns:
        Formatted entries or error message
    """
    from memory.memory_manager import memory_manager

    error = validate_tier(tier, ANY_TIERS)
    if error:
        return error

    try:
        entries = await memory_manager.recent(
            SESSION_ROLE,
            limit=limit,
            tier=tier,
        )
    except Exception as exc:
        return format_memory_error("memory_recent", exc)

    if not entries:
        return format_no_results()

    return format_entries(entries, max_content_len=150)


async def _debug_trace_handler(
    query: str,
    start_datetime: str | None = None,
    end_datetime: str | None = None,
) -> str:
    """Search each tier separately; show match counts with optional time filter.

    MEMORY ACCESS:
    - GOAT supervisor: Full tier access (working, episodic, long_term)
    - DAG agents: Working tier only (enforced automatically)

    Args:
        query: Semantic search query
        start_datetime: Optional ISO 8601 or natural-language start
        end_datetime: Optional ISO 8601 or natural-language end

    Returns:
        JSON-formatted debug trace results
    """
    from memory.memory_manager import memory_manager

    try:
        result = await memory_manager.debug_trace(
            GOAT_ROLE,
            query,
            start_datetime,
            end_datetime,
        )
    except Exception as exc:
        return format_memory_error("memory_debug_trace", exc)

    return json.dumps(result, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

MEMORY_TIMELINE = ToolDefinition(
    name="memory_timeline",
    description="Return entries from a specific time window, newest first.",
    parameters={
        "type": "object",
        "required": ["start_datetime", "end_datetime"],
        "properties": {
            "start_datetime": {
                "type": "string",
                "description": "ISO 8601 or natural language (e.g. 'yesterday').",
            },
            "end_datetime": {
                "type": "string",
                "description": "ISO 8601 or natural-language end bound.",
            },
            "tier": {
                "type": "string",
                "enum": list(ANY_TIERS),
                "default": "any",
            },
            "limit": {
                "type": "integer",
                "description": "Max results (default 100).",
                "default": 100,
            },
        },
    },
    handler=_timeline_handler,
)

MEMORY_RECENT = ToolDefinition(
    name="memory_recent",
    description="Return the N most recent memory entries, newest first.",
    parameters={
        "type": "object",
        "required": [],
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Max results (default 50).",
                "default": 50,
            },
            "tier": {
                "type": "string",
                "enum": list(ANY_TIERS),
                "default": "any",
            },
        },
    },
    handler=_recent_handler,
)

MEMORY_DEBUG_TRACE = ToolDefinition(
    name="memory_debug_trace",
    description="Search each tier separately; show match counts.",
    parameters={
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {
                "type": "string",
                "description": "Semantic search query.",
            },
            "start_datetime": {
                "type": "string",
                "description": "Optional ISO 8601 or natural-language start.",
            },
            "end_datetime": {
                "type": "string",
                "description": "Optional ISO 8601 or natural-language end.",
            },
        },
    },
    handler=_debug_trace_handler,
)
