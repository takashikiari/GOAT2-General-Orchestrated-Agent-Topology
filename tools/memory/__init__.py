"""Memory operation tools — CRUD, temporal queries, and management.

This module provides memory operations across three tiers:
- working: Redis-backed, session-scoped, TTL-enforced
- episodic: ChromaDB persistent, semantic search
- long_term: Letta core-memory blocks, most persistent

MEMORY ACCESS ARCHITECTURE:
===========================
- GOAT (supervisor): Full tier access with GOAT_ROLE from config.roles
- DAG agents: Working tier only with SESSION_ROLE from config.roles
- Validation: All writes validated via memory.validation module

TOOL EXPORTS:
============
- MEMORY_SEARCH: Semantic search across tiers
- MEMORY_GET: Exact-key lookup
- MEMORY_STORE: Key-value storage
- MEMORY_DELETE: Delete entry by key
- MEMORY_UPDATE: Update or upsert entry
- MEMORY_TIMELINE: Time-based query
- MEMORY_RECENT: Most recent entries
- MEMORY_DEBUG_TRACE: Per-tier debug search
- MEMORY_DIRECT_QUERY: Raw query syntax
- MEMORY_LAST_WRITE: Check last write timestamp
- MEMORY_COUNT: Count entries per tier
- MEMORY_TTL: Check remaining TTL
- MEMORY_EXPORT: Bulk export as JSON
- MEMORY_PROMOTE: Move between tiers
- MEMORY_AUTO_PROMOTE: Bulk promotion
- MEMORY_EMBEDDING: Get embedding vectors

ALSO EXPORTS:
============
- memory_helpers: Shared utilities (format_memory_error, validate_tier, etc.)
- DAG variants: MEMORY_SEARCH_DAG, MEMORY_GET_DAG, MEMORY_STORE_DAG
"""

from __future__ import annotations

from tools.memory.memory_auto_promote_tool import MEMORY_AUTO_PROMOTE
from tools.memory.memory_count_tool import MEMORY_COUNT
from tools.memory.memory_delete_tool import MEMORY_DELETE
from tools.memory.memory_direct_query import MEMORY_DIRECT_QUERY
from tools.memory.memory_embedding_tool import MEMORY_EMBEDDING
from tools.memory.memory_export_tool import MEMORY_EXPORT
from tools.memory.memory_last_write import MEMORY_LAST_WRITE
from tools.memory.memory_promote_tool import MEMORY_PROMOTE
from tools.memory.memory_temporal_tools import (
    MEMORY_DEBUG_TRACE,
    MEMORY_RECENT,
    MEMORY_RECENT_DAG,
    MEMORY_TIMELINE,
)
from tools.memory.memory_tools import (
    MEMORY_GET,
    MEMORY_SEARCH,
    MEMORY_STORE,
    MEMORY_GET_DAG,
    MEMORY_SEARCH_DAG,
    MEMORY_STORE_DAG,
)
from tools.memory.memory_ttl_tool import MEMORY_TTL
from tools.memory.memory_update_tool import MEMORY_UPDATE

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