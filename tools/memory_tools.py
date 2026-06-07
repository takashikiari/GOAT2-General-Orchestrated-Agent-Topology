"""Memory CRUD tools — search, get, and store across memory tiers.

Provides three ToolDefinition constants (MEMORY_SEARCH, MEMORY_GET,
MEMORY_STORE) for semantic search, exact-key retrieval, and key-value
storage across working, episodic, and long-term memory tiers.

MEMORY ACCESS ARCHITECTURE:
===========================
- GOAT (supervisor): Full tier access with role="goat"
- DAG agents: Working tier only with role="user_session"
- Validation: All writes validated via memory.validation module
- Sanitization: Content sanitized before storage

TOOL WIRING:
============
- MEMORY_SEARCH: Supervisor uses role="goat", agents use role="user_session"
- MEMORY_GET: Supervisor uses role="goat", agents use role="user_session"
- MEMORY_STORE: Supervisor can write to all tiers, agents to working only

Refactored to use memory_helpers.py for shared logic (stays under 200 lines).
"""
from __future__ import annotations

from agents.base_agent import ToolDefinition
from memory.validation import sanitize_content, validate_memory_write
from tools.memory_helpers import (
    ANY_TIERS,
    ALL_TIERS,
    GOAT_ROLE,
    DAG_AGENT_ROLE,
    format_entries,
    format_memory_error,
    format_no_results,
    validate_tier,
)

__all__ = ["MEMORY_SEARCH", "MEMORY_GET", "MEMORY_STORE"]

# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


async def _search_handler(
    query: str,
    limit: int = 20,
    start_datetime: str | None = None,
    end_datetime: str | None = None,
    tier: str = "any",
    role: str = GOAT_ROLE,
) -> str:
    """Semantic search across memory tiers with optional time window.

    MEMORY ACCESS:
    - GOAT supervisor: role="goat" for full tier access
    - DAG agents: role="user_session" with tier="working" only

    Args:
        query: Semantic search query
        limit: Max results (default 20)
        start_datetime: ISO 8601 or natural-language start
        end_datetime: ISO 8601 or natural-language end bound
        tier: Tier to search (default: 'any')
        role: Caller role ('goat' or 'user_session')

    Returns:
        Formatted search results or error message
    """
    from memory.memory_manager import memory_manager

    error = validate_tier(tier, ANY_TIERS)
    if error:
        return error

    # Enforce tier restriction for DAG agents
    if role == DAG_AGENT_ROLE and tier not in ("working", "any"):
        return f"ERROR: DAG agents can only access working tier, not {tier!r}"

    try:
        kw = {} if tier == "any" else {"memory_type": tier}
        entries = await memory_manager.search(
            role,
            query,
            limit=limit,
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            **kw,
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
    role: str = GOAT_ROLE,
) -> str:
    """Retrieve a memory entry by exact key.

    MEMORY ACCESS:
    - GOAT supervisor: role="goat" for full tier access
    - DAG agents: role="user_session" with tier="working" only

    Args:
        key: Exact memory key
        tier: Tier to probe (default: 'any')
        role: Caller role ('goat' or 'user_session')

    Returns:
        Entry content or error message
    """
    from memory.memory_manager import memory_manager

    error = validate_tier(tier, ANY_TIERS)
    if error:
        return error

    # Enforce tier restriction for DAG agents
    if role == DAG_AGENT_ROLE and tier not in ("working", "any"):
        return f"ERROR: DAG agents can only access working tier, not {tier!r}"

    try:
        kw = {} if tier == "any" else {"memory_type": tier}
        entry = await memory_manager.locate(role, key, **kw)
    except Exception as exc:
        return format_memory_error("memory_get", exc)

    return entry.content if entry else f"No entry found for key: {key!r}"


async def _store_handler(
    key: str,
    value: str,
    tier: str = "working",
    role: str = GOAT_ROLE,
) -> str:
    """Store a key-value pair in a memory tier (default: working/Redis).

    MEMORY ACCESS:
    - GOAT supervisor: Can write to all tiers (working, episodic, long_term)
    - DAG agents: Can only write to working tier

    Includes validation and sanitization to prevent garbage data.

    Args:
        key: Memory key
        value: Content to store
        tier: Target tier (default: 'working')
        role: Caller role ('goat' or 'user_session')

    Returns:
        Success message or error string
    """
    from memory.memory_manager import memory_manager

    error = validate_tier(tier, ALL_TIERS)
    if error:
        return error

    # Enforce tier restriction for DAG agents
    if role == DAG_AGENT_ROLE and tier != "working":
        return f"ERROR: DAG agents can only write to working tier, not {tier!r}"

    # Validate and sanitize before storing
    try:
        validate_memory_write(key, value, tier)
        value = sanitize_content(value)
    except ValueError as exc:
        return f"ERROR: validation failed: {exc}"

    try:
        await memory_manager.store(role, key, value, memory_type=tier)
    except Exception as exc:
        return format_memory_error("memory_store", exc)

    return f"Stored {key!r} in {tier}"


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

MEMORY_SEARCH = ToolDefinition(
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
                "enum": list(ANY_TIERS),
                "description": "Tier to search (default: 'any').",
                "default": "any",
            },
        },
    },
    handler=_search_handler,
)

MEMORY_GET = ToolDefinition(
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

MEMORY_STORE = ToolDefinition(
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
