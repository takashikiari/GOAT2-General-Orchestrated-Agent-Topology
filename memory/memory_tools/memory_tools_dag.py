"""DAG-restricted memory tools — search, get, and store in working tier only.

Provides three ToolDefinition constants (MEMORY_SEARCH_DAG, MEMORY_GET_DAG,
MEMORY_STORE_DAG) for DAG agents that have no direct access to episodic
or long-term memory tiers. Forces SESSION_ROLE and memory_type=working.

These are intentionally separate from the GOAT-facing tools in
``memory_tools.py`` to keep each file under the 260-line ceiling.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from config.roles import SESSION_ROLE
from memory.shared.validation import sanitize_content, validate_memory_write
from memory.memory_tools.memory_helpers import (
    make_tool,
    format_entries,
    format_memory_error,
    format_no_results
)

if TYPE_CHECKING:
    from memory.shared.memory_manager import MemoryManager

log = logging.getLogger("goat2.memory.tools")

__all__ = ["MEMORY_SEARCH_DAG", "MEMORY_GET_DAG", "MEMORY_STORE_DAG"]


# ---------------------------------------------------------------------------
# DAG Handlers (working tier only)
# ---------------------------------------------------------------------------


async def _search_handler_dag(
    query: str,
    limit: int = 20,
    memory_manager: "MemoryManager | None" = None,
) -> str:
    """Semantic search in working memory only (DAG-restricted)."""
    if memory_manager is None:
        from tools.registry_accessor import get_registry
        memory_manager = get_registry().memory_manager
    log.debug("memory_search_dag: query=%r limit=%d", query[:60], limit)
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
    """Retrieve a memory entry from working memory only (DAG-restricted)."""
    if memory_manager is None:
        from tools.registry_accessor import get_registry
        memory_manager = get_registry().memory_manager
    log.debug("memory_get_dag: key=%r", key)
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
    """Store a key-value pair in working memory only (DAG-restricted)."""
    if memory_manager is None:
        from tools.registry_accessor import get_registry
        memory_manager = get_registry().memory_manager
    log.debug("memory_store_dag: key=%r (len=%d)", key, len(value))

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
# DAG Tool definitions
# ---------------------------------------------------------------------------

MEMORY_SEARCH_DAG = make_tool(
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

MEMORY_GET_DAG = make_tool(
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

MEMORY_STORE_DAG = make_tool(
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
