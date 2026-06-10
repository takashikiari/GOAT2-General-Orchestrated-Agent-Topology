"""Memory CRUD tools — search, get, and store across memory tiers.

Provides three ToolDefinition constants (MEMORY_SEARCH, MEMORY_GET,
MEMORY_STORE) for semantic search, exact-key retrieval, and key-value
storage across working, episodic, and long-term memory tiers.

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

from typing import TYPE_CHECKING

from agents.base_agent import ToolDefinition
from config.roles import GOAT_ROLE, SESSION_ROLE
from memory.validation import sanitize_content, validate_memory_write
from tools.memory.memory_helpers import (
    ANY_TIERS,
    ALL_TIERS,
    format_entries,
    format_memory_error,
    format_no_results,
    validate_tier,
)
from tools.registry_accessor import get_registry

if TYPE_CHECKING:
    from memory.memory_manager import MemoryManager

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
        memory_manager = get_registry().memory_manager

    error = validate_tier(tier, ANY_TIERS)
    if error:
        return error

    try:
        kw = {} if tier == "any" else {"memory_type": tier}
        entries = await memory_manager.search(
            GOAT_ROLE,
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
    memory_manager: "MemoryManager | None" = None,
) -> str:
    """Retrieve a memory entry by exact key.

    MEMORY ACCESS: GOAT supervisor has full tier access.
    """
    if memory_manager is None:
        memory_manager = get_registry().memory_manager

    error = validate_tier(tier, ANY_TIERS)
    if error:
        return error

    try:
        kw = {} if tier == "any" else {"memory_type": tier}
        entry = await memory_manager.locate(GOAT_ROLE, key, **kw)
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
        memory_manager = get_registry().memory_manager

    error = validate_tier(tier, ALL_TIERS)
    if error:
        return error

    # Validate and sanitize before storing
    try:
        validate_memory_write(key, value, tier)
        value = sanitize_content(value)
    except ValueError as exc:
        return f"ERROR: validation failed: {exc}"

    try:
        await memory_manager.store(GOAT_ROLE, key, value, memory_type=tier)
    except Exception as exc:
        return format_memory_error("memory_store", exc)

    return f"Stored {key!r} in {tier}"


# ---------------------------------------------------------------------------
# DAG Handlers (working tier only)
# ---------------------------------------------------------------------------


async def _search_handler_dag(
    query: str,
    limit: int = 20,
    memory_manager: "MemoryManager | None" = None,
) -> str:
    """Semantic search in working memory only (DAG-restricted).

    DAG agents can only access working tier for session context.
    """
    if memory_manager is None:
        memory_manager = get_registry().memory_manager

    try:
        entries = await memory_manager.search(
            SESSION_ROLE,
            query,
            limit=limit,
            memory_type="working",
        )
    except Exception as exc:
        return format_memory_error("memory_search", exc)

    if not entries:
        return format_no_results(f"for: {query!r}")

    return format_entries(entries, max_content_len=200)


async def _get_handler_dag(
    key: str,
    memory_manager: "MemoryManager | None" = None,
) -> str:
    """Retrieve a memory entry from working memory only (DAG-restricted).

    DAG agents can only access working tier for session context.
    """
    if memory_manager is None:
        memory_manager = get_registry().memory_manager

    try:
        entry = await memory_manager.locate(SESSION_ROLE, key, memory_type="working")
    except Exception as exc:
        return format_memory_error("memory_get", exc)

    return entry.content if entry else f"No entry found for key: {key!r}"


async def _store_handler_dag(
    key: str,
    value: str,
    memory_manager: "MemoryManager | None" = None,
) -> str:
    """Store a key-value pair in working memory only (DAG-restricted).

    DAG agents can only write to working tier for session context.
    """
    if memory_manager is None:
        memory_manager = get_registry().memory_manager

    # Validate and sanitize before storing
    try:
        validate_memory_write(key, value, "working")
        value = sanitize_content(value)
    except ValueError as exc:
        return f"ERROR: validation failed: {exc}"

    try:
        await memory_manager.store(SESSION_ROLE, key, value, memory_type="working")
    except Exception as exc:
        return format_memory_error("memory_store", exc)

    return f"Stored {key!r} in working"


# ---------------------------------------------------------------------------
# Tool definitions (GOAT - full access)
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


# ---------------------------------------------------------------------------
# DAG Tool definitions (working tier only)
# ---------------------------------------------------------------------------

MEMORY_SEARCH_DAG = ToolDefinition(
    name="memory_search",
    description="Semantic search in working memory only (DAG agents).",
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
        },
    },
    handler=_search_handler_dag,
)

MEMORY_GET_DAG = ToolDefinition(
    name="memory_get",
    description="Retrieve a memory entry from working memory by exact key (DAG agents).",
    parameters={
        "type": "object",
        "required": ["key"],
        "properties": {
            "key": {
                "type": "string",
                "description": "Exact memory key.",
            },
        },
    },
    handler=_get_handler_dag,
)

MEMORY_STORE_DAG = ToolDefinition(
    name="memory_store",
    description="Store a key-value pair in working memory (DAG agents).",
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
        },
    },
    handler=_store_handler_dag,
)