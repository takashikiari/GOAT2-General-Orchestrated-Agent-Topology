"""Memory CRUD tools — search, get, and store across memory tiers (GOAT-facing).

Provides three ToolDefinition constants (MEMORY_SEARCH, MEMORY_GET,
MEMORY_STORE) for semantic search, exact-key retrieval, and key-value
storage across working, episodic, and long-term memory tiers.

DAG-restricted variants live in ``memory_tools_dag.py`` (working tier only).

MEMORY ACCESS ARCHITECTURE:
===========================
- GOAT (supervisor): Full tier access with GOAT_ROLE from config.roles
- DAG agents: Working tier only with SESSION_ROLE from config.roles
- Validation: All writes validated via memory.validation module
- Sanitization: Content sanitized before storage

TOOL WIRING:
============
- GOAT/supervisor: Uses MEMORY_SEARCH, MEMORY_GET, MEMORY_STORE (full access)
- DAG agents: Uses dag_memory_tools list with DAG-restricted handlers
- DAG handlers force tier=working and use SESSION_ROLE internally
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from memory.shared.validation import sanitize_content, validate_memory_write
from memory.memory_tools.memory_helpers import (
    make_tool,
    ANY_TIERS,
    ALL_TIERS,
    SEARCH_TIERS,
    format_entries,
    format_memory_error,
    format_no_results,
    letta_search_safe,
    normalize_tier,
    role_for_tier,
    validate_tier,
)

if TYPE_CHECKING:
    from memory.shared.memory_manager import MemoryManager

log = logging.getLogger("goat2.memory.tools")

__all__ = ["MEMORY_SEARCH", "MEMORY_GET", "MEMORY_STORE"]


# ---------------------------------------------------------------------------
# GOAT Handlers (full tier access)
# ---------------------------------------------------------------------------


async def _search_handler(
    query: str,
    limit: int = 20,
    start_datetime: str | None = None,
    end_datetime: str | None = None,
    tier: str = "any",
    memory_manager: "MemoryManager | None" = None,
) -> str:
    """Semantic search across memory tiers with optional time window.

    MEMORY ACCESS: GOAT supervisor has full tier access.
    """
    if memory_manager is None:
        from tools.registry_accessor import get_registry
        memory_manager = get_registry().memory_manager

    error = validate_tier(tier, SEARCH_TIERS)
    if error:
        return error
    # Normalise user-facing tier aliases before touching MemoryManager.
    # "letta" -> "long_term" (the only valid MemoryType for Letta);
    # "all" -> "any" (triggers MemoryManager's 3-tier fan-out).
    normalized = normalize_tier(tier)
    log.debug(
        "memory_search: tier=%s (normalised=%s) query=%r limit=%d",
        tier, normalized, query[:60], limit,
    )

    try:
        if normalized == "any":
            # memory_type=None triggers MemoryManager._fan_out_search
            # which queries all three tiers in parallel and dedupes.
            entries = await memory_manager.search(
                role_for_tier(tier), query, limit=limit,
                start_datetime=start_datetime, end_datetime=end_datetime,
            )
        elif normalized == "long_term":
            # Direct Letta call through the 10 s safety wrapper.
            # role_for_tier("long_term") already returns GOAT_ROLE; the
            # wrapper uses GOAT_ROLE internally as well.
            entries = await letta_search_safe(memory_manager, query, limit)
        else:
            # working / episodic — single-tier search via the manager so
            # temporal filtering / dedup logic applies.
            entries = await memory_manager.search(
                role_for_tier(tier), query, limit=limit,
                memory_type=normalized,
                start_datetime=start_datetime, end_datetime=end_datetime,
            )
    except Exception as exc:
        return format_memory_error("memory_search", exc)

    if not entries:
        rng = f"[{start_datetime}→{end_datetime}] " if (start_datetime or end_datetime) else ""
        return format_no_results(f"{rng}for: {query!r}")

    return format_entries(entries, max_content_len=200)


async def _get_handler(
    key: str,
    tier: str = "any",
    memory_manager: "MemoryManager | None" = None,
) -> str:
    """Retrieve a memory entry by exact key.

    MEMORY ACCESS: GOAT supervisor has full tier access.
    """
    if memory_manager is None:
        from tools.registry_accessor import get_registry
        memory_manager = get_registry().memory_manager

    error = validate_tier(tier, ANY_TIERS)
    if error:
        return error
    log.debug("memory_get: tier=%s key=%r", tier, key)

    try:
        kw = {} if tier == "any" else {"memory_type": tier}
        entry = await memory_manager.locate(role_for_tier(tier), key, **kw)
    except Exception as exc:
        return format_memory_error("memory_get", exc)

    return entry.content if entry else f"No entry found for key: {key!r}"


async def _store_handler(
    key: str,
    value: str,
    tier: str = "working",
    memory_manager: "MemoryManager | None" = None,
) -> str:
    """Store a key-value pair in a memory tier (default: working/Redis).

    MEMORY ACCESS: GOAT supervisor can write to all tiers.
    """
    if memory_manager is None:
        from tools.registry_accessor import get_registry
        memory_manager = get_registry().memory_manager

    error = validate_tier(tier, ALL_TIERS)
    if error:
        return error
    log.debug("memory_store: tier=%s key=%r (len=%d)", tier, key, len(value))

    # Validate and sanitize before storing
    try:
        validate_memory_write(key, value, tier)
        value = sanitize_content(value)
    except ValueError as exc:
        return f"ERROR: validation failed: {exc}"

    try:
        await memory_manager.store(role_for_tier(tier), key, value, memory_type=tier)
    except Exception as exc:
        return format_memory_error("memory_store", exc)

    return f"Stored {key!r} in {tier}"


# ---------------------------------------------------------------------------
# Tool definitions (GOAT - full access)
# ---------------------------------------------------------------------------

MEMORY_SEARCH = make_tool(
    name="memory_search",
    description="Semantic search across memory tiers with optional time window.",
    parameters={
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {
                "type": "string",
                "description": "Semantic search query.",
            },
            "limit": {
                "type": "integer",
                "description": "Max results (default 20).",
                "default": 20,
            },
            "start_datetime": {
                "type": "string",
                "description": "ISO 8601 or natural-language start.",
            },
            "end_datetime": {
                "type": "string",
                "description": "ISO 8601 or natural-language end bound.",
            },
            "tier": {
                "type": "string",
                "enum": list(SEARCH_TIERS),
                "description": "Tier to search (default: 'any').",
                "default": "any",
            },
        },
    },
    handler=_search_handler,
)

MEMORY_GET = make_tool(
    name="memory_get",
    description="Retrieve a memory entry by exact key.",
    parameters={
        "type": "object",
        "required": ["key"],
        "properties": {
            "key": {
                "type": "string",
                "description": "Exact memory key.",
            },
            "tier": {
                "type": "string",
                "enum": list(ANY_TIERS),
                "description": "Tier to probe (default: 'any').",
                "default": "any",
            },
        },
    },
    handler=_get_handler,
)

MEMORY_STORE = make_tool(
    name="memory_store",
    description="Store a key-value pair in a memory tier (default: working).",
    parameters={
        "type": "object",
        "required": ["key", "value"],
        "properties": {
            "key": {
                "type": "string",
                "description": "Memory key.",
            },
            "value": {
                "type": "string",
                "description": "Content to store.",
            },
            "tier": {
                "type": "string",
                "enum": list(ALL_TIERS),
                "description": "Target tier (default: 'working').",
                "default": "working",
            },
        },
    },
    handler=_store_handler,
)
