"""Memory operation tools — shim that re-exports from memory.memory_tools.

This module is kept for backward compatibility. New code should import
from memory.memory_tools instead.

TOOL EXPORTS:
============
All tools re-exported from memory.memory_tools.
"""

from __future__ import annotations

# Re-export from memory.memory_tools
from memory.memory_tools import (
    MEMORY_AUTO_PROMOTE,
    MEMORY_COUNT,
    MEMORY_DELETE,
    MEMORY_DIRECT_QUERY,
    MEMORY_EMBEDDING,
    MEMORY_EXPORT,
    MEMORY_GET,
    MEMORY_GET_DAG,
    MEMORY_LAST_WRITE,
    MEMORY_PROMOTE,
    MEMORY_RECENT,
    MEMORY_RECENT_DAG,
    MEMORY_SEARCH,
    MEMORY_SEARCH_DAG,
    MEMORY_STORE,
    MEMORY_STORE_DAG,
    MEMORY_TIMELINE,
    MEMORY_DEBUG_TRACE,
    MEMORY_TTL,
    MEMORY_UPDATE,
)

__all__ = [
    # GOAT tools (full tier access)
    "MEMORY_SEARCH",
    "MEMORY_GET",
    "MEMORY_STORE",
    "MEMORY_DELETE",
    "MEMORY_UPDATE",
    "MEMORY_TIMELINE",
    "MEMORY_RECENT",
    "MEMORY_DEBUG_TRACE",
    "MEMORY_DIRECT_QUERY",
    "MEMORY_LAST_WRITE",
    "MEMORY_COUNT",
    "MEMORY_TTL",
    "MEMORY_EMBEDDING",
    "MEMORY_EXPORT",
    "MEMORY_PROMOTE",
    "MEMORY_AUTO_PROMOTE",
    # DAG tools (working tier only)
    "MEMORY_SEARCH_DAG",
    "MEMORY_GET_DAG",
    "MEMORY_STORE_DAG",
    "MEMORY_RECENT_DAG",
]