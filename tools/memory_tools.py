"""Memory CRUD tools — search, get, and store across memory tiers.

Provides three ToolDefinition constants (MEMORY_SEARCH, MEMORY_GET,
MEMORY_STORE) for semantic search, exact-key retrieval, and key-value
storage across working, episodic, and long-term memory tiers.

GOAT (supervisor) has full tier access with role="goat".
DAG agents are restricted to working tier only with role="user_session".
"""

from __future__ import annotations

from typing import Final

from agents.base_agent import ToolDefinition

__all__ = ["MEMORY_SEARCH", "MEMORY_GET", "MEMORY_STORE"]

_ROLE: Final[str] = "goat"
_TIERS: Final[tuple[str, ...]] = ("working", "episodic", "long_term")
_ANY_TIERS: Final[tuple[str, ...]] = ("any",) + _TIERS


async def _search_handler(
    query: str, limit: int = 20,
    start_datetime: str | None = None, end_datetime: str | None = None,
    tier: str = "any",
) -> str:
    """Semantic search across memory tiers with optional time window.

    GOAT supervisor uses role="goat" for full tier access.
    DAG agents should use tier="working" only (enforced by system prompt).
    """
    from memory.memory_manager import memory_manager
    try:
        kw: dict = {} if tier == "any" else {"memory_type": tier}
        entries = await memory_manager.search(
            _ROLE, query, limit=limit,
            start_datetime=start_datetime, end_datetime=end_datetime, **kw
        )
    except Exception as exc:
        return f"ERROR: memory_search failed: {exc}"
    if not entries:
        rng = f" [{start_datetime}→{end_datetime}]" if (start_datetime or end_datetime) else ""
        return f"No memory found{rng} for: {query!r}"
    return "\n".join(f"[{e.source}] {e.key}: {e.content[:200]}" for e in entries)


async def _get_handler(key: str, tier: str = "any") -> str:
    """Retrieve a memory entry by exact key. Use tier='any' to probe all layers.

    GOAT supervisor uses role="goat" for full tier access.
    DAG agents should use tier="working" only (enforced by system prompt).
    """
    from memory.memory_manager import memory_manager
    try:
        kw = {} if tier == "any" else {"memory_type": tier}
        entry = await memory_manager.locate(_ROLE, key, **kw)
    except Exception as exc:
        return f"ERROR: memory_get failed: {exc}"
    return entry.content if entry else f"No entry found for key: {key!r}"


async def _store_handler(key: str, value: str, tier: str = "working") -> str:
    """Store a key-value pair in a memory tier (default: working/Redis).

    GOAT supervisor uses role="goat" for full tier access.
    DAG agents should use tier="working" only (enforced by system prompt).
    """
    from memory.memory_manager import memory_manager
    if tier not in _TIERS:
        return f"ERROR: invalid tier '{tier}'; valid: {_TIERS}"
    try:
        await memory_manager.store(_ROLE, key, value, memory_type=tier)
    except Exception as exc:
        return f"ERROR: memory_store failed: {exc}"
    return f"Stored {key!r} in {tier}"


MEMORY_SEARCH = ToolDefinition(
    name="memory_search",
    description="Semantic search across memory tiers with optional time window. GOAT has full access; DAG agents restricted to working tier.",
    parameters={"type": "object", "required": ["query"], "properties": {
        "query": {"type": "string", "description": "Semantic search query."},
        "limit": {"type": "integer", "description": "Max results (default 20).", "default": 20},
        "start_datetime": {"type": "string", "description": "ISO 8601 or natural-language start (e.g. 'yesterday morning', 'last 24h')."},
        "end_datetime": {"type": "string", "description": "ISO 8601 or natural-language end bound."},
        "tier": {"type": "string", "enum": list(_ANY_TIERS), "description": "Tier to search (default: 'any'). GOAT: any; DAG: working only.", "default": "any"},
    }},
    handler=_search_handler,
)

MEMORY_GET = ToolDefinition(
    name="memory_get",
    description="Retrieve a memory entry by exact key. Use tier='any' to probe all layers. GOAT has full access; DAG agents restricted to working tier.",
    parameters={"type": "object", "required": ["key"], "properties": {
        "key":  {"type": "string", "description": "Exact memory key."},
        "tier": {"type": "string", "enum": list(_ANY_TIERS), "description": "Tier to probe (default: 'any' = all). GOAT: any; DAG: working only.", "default": "any"},
    }},
    handler=_get_handler,
)

MEMORY_STORE = ToolDefinition(
    name="memory_store",
    description="Store a key-value pair in a memory tier (default: working/Redis). GOAT has full access; DAG agents restricted to working tier.",
    parameters={"type": "object", "required": ["key", "value"], "properties": {
        "key":   {"type": "string", "description": "Memory key."},
        "value": {"type": "string", "description": "Content to store."},
        "tier":  {"type": "string", "enum": list(_TIERS), "description": "Target tier (default: 'working'). GOAT: any; DAG: working only.", "default": "working"},
    }},
    handler=_store_handler,
)
