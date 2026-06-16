"""Memory debug-trace tool — per-tier match counts for a search query.

Provides MEMORY_DEBUG_TRACE ToolDefinition for supervisor. Useful for
introspecting which memory tiers actually contain results for a given
query — separate from the time-window and recency tools that live in
``memory_temporal_tools.py`` to keep that file under the 260-line ceiling.

MEMORY ACCESS ARCHITECTURE:
===========================
- GOAT (supervisor): Full tier access with GOAT_ROLE from config.roles
- DAG agents: Working tier only (enforced automatically)
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from config.roles import GOAT_ROLE
from memory.memory_tools.memory_helpers import format_memory_error, make_tool

if TYPE_CHECKING:
    from memory.shared.memory_manager import MemoryManager

log = logging.getLogger("goat2.memory.tools")

__all__ = ["MEMORY_DEBUG_TRACE"]


async def _debug_trace_handler(
    query: str,
    start_datetime: str | None = None,
    end_datetime: str | None = None,
    memory_manager: "MemoryManager | None" = None,
) -> str:
    """Search each tier separately; show match counts with optional time filter.

    Args:
        query: Semantic search query
        start_datetime: Optional ISO 8601 or natural-language start
        end_datetime: Optional ISO 8601 or natural-language end
        memory_manager: Optional injected MemoryManager

    Returns:
        JSON-formatted debug trace results
    """
    if memory_manager is None:
        from tools.registry_accessor import get_registry
        registry = get_registry()
        memory_manager = registry.memory_manager
    log.debug(
        "memory_debug_trace: query=%r range=(%r,%r)",
        query[:60], start_datetime, end_datetime,
    )

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


MEMORY_DEBUG_TRACE = make_tool(
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
