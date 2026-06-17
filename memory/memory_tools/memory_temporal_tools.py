"""Temporal memory query tools — timeline and recent (GOAT + DAG).

Provides two ToolDefinition constants (MEMORY_TIMELINE, MEMORY_RECENT,
MEMORY_RECENT_DAG) for time-based memory queries.

The MEMORY_DEBUG_TRACE tool was moved to ``memory_debug_trace_tool.py``
to keep this file under the 260-line ceiling.

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
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from config.roles import SESSION_ROLE
from config.tiers import ANY
from memory.memory_tools.memory_helpers import (
    make_tool,
    ANY_TIERS,
    SEARCH_TIERS,
    format_entries,
    format_memory_error,
    format_no_results,
    letta_list_safe,
    normalize_tier,
    role_for_tier,
    validate_tier,
)

if TYPE_CHECKING:
    from memory.shared.memory_manager import MemoryManager

log = logging.getLogger("goat2.memory.tools")

__all__ = ["MEMORY_TIMELINE", "MEMORY_RECENT", "MEMORY_RECENT_DAG"]


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


async def _timeline_handler(
    start_datetime: str,
    end_datetime: str,
    tier: str = ANY,
    limit: int = 100,
    memory_manager: "MemoryManager | None" = None,
) -> str:
    """Return entries from a specific time window, newest first.

    Args:
        start_datetime: ISO 8601 or natural language (e.g. 'yesterday')
        end_datetime: ISO 8601 or natural-language end bound
        tier: Tier to query (default: 'any')
        limit: Max results (default 100)
        memory_manager: Optional injected MemoryManager

    Returns:
        Formatted entries or error message
    """
    if memory_manager is None:
        from tools.registry_accessor import get_registry
        registry = get_registry()
        memory_manager = registry.memory_manager

    error = validate_tier(tier, SEARCH_TIERS)
    if error:
        return error
    log.debug(
        "memory_timeline: tier=%s range=(%r,%r) limit=%d",
        tier, start_datetime, end_datetime, limit,
    )

    try:
        entries = await memory_manager.timeline(
            role_for_tier(tier),
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


async def _recent_handler_dag(limit: int = 50, **kwargs) -> str:
    """Wrapper that forces tier=working for DAG agents."""
    return await _recent_handler(limit=int(limit), tier="working")


async def _recent_handler(
    limit: int = 50,
    tier: str = ANY,
    memory_manager: "MemoryManager | None" = None,
) -> str:
    """Return the N most recent memory entries, newest first.

    Args:
        limit: Max results (default 50)
        tier: Tier to query (default: 'any')
        memory_manager: Optional injected MemoryManager

    Returns:
        Formatted entries or error message
    """
    if memory_manager is None:
        from tools.registry_accessor import get_registry
        registry = get_registry()
        memory_manager = registry.memory_manager

    error = validate_tier(tier, SEARCH_TIERS)
    if error:
        return error
    # Normalise user-facing tier aliases ("letta" -> "long_term",
    # "all" -> "any") before reaching gather_tier_list, which would
    # otherwise raise ValueError on "letta" via MemoryType().
    normalized = normalize_tier(tier)
    log.debug("memory_recent: tier=%s (normalised=%s) limit=%d", tier, normalized, limit)

    try:
        if normalized == "long_term":
            # Direct Letta call through the 10 s safety wrapper — bypasses
            # gather_tier_list entirely so MemoryType() coercion is never hit.
            entries = await letta_list_safe(memory_manager, limit)
        else:
            # working / episodic / any → existing gather_tier_list path.
            # "any" / "all" already fan out across all three tiers there,
            # so the user gets results from every tier in one call.
            entries = await memory_manager.recent(
                SESSION_ROLE,
                limit=limit,
                tier=normalized,
            )
    except Exception as exc:
        return format_memory_error("memory_recent", exc)

    if not entries:
        return format_no_results()

    return format_entries(entries, max_content_len=150)


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

MEMORY_TIMELINE = make_tool(
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
                "enum": list(SEARCH_TIERS),
                "default": ANY,
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

MEMORY_RECENT_DAG = make_tool(
    name="memory_recent",
    description="Return the N most recent working memory entries, newest first. DAG-safe: only searches working tier (Redis).",
    parameters={
        "type": "object",
        "required": [],
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Max results (default 50).",
                "default": 50,
            },
        },
    },
    handler=_recent_handler_dag,
)

MEMORY_RECENT = make_tool(
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
                "enum": list(SEARCH_TIERS),
                "default": ANY,
            },
        },
    },
    handler=_recent_handler,
)
