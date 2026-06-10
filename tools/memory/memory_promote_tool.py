"""Memory promotion tool — move entries between tiers.

Provides MEMORY_PROMOTE ToolDefinition for supervisor to promote
entries from one memory tier to another on request.

BEHAVIOR:
=========
- Key-based promote: For working and episodic tiers
- Query-based promote: For long_term tier (Letta uses blocks, not simple keys)
  When promoting FROM long_term, specify a query to find the entry instead of a key
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from agents.base_agent import ToolDefinition
from config.roles import GOAT_ROLE
from config.tiers import WORKING, EPISODIC, LONG_TERM
from tools.memory.memory_helpers import format_memory_error, format_entries, ANY_TIERS
from tools.registry_accessor import get_registry

if TYPE_CHECKING:
    from memory.memory_manager import MemoryManager

__all__ = ["MEMORY_PROMOTE"]


async def _promote_handler(
    key: str,
    from_tier: str,
    to_tier: str,
    keep_source: bool = False,
    query: str | None = None,
    memory_manager: "MemoryManager | None" = None,
) -> str:
    """Promote a memory entry between tiers.

    For long_term (Letta) source: uses query-based promotion if key not found.
    For working/episodic source: standard key-based promotion.

    Args:
        key: Memory key to promote (ignored for long_term if query provided)
        from_tier: Source tier (working, episodic, long_term)
        to_tier: Destination tier (working, episodic, long_term)
        keep_source: Whether to keep source after promote (default: False = move)
        query: For long_term source: search query to find entry by content
        memory_manager: Optional injected MemoryManager

    Returns:
        Success message or error string
    """
    valid_tiers = (WORKING, EPISODIC, LONG_TERM)
    if from_tier not in valid_tiers:
        return f"ERROR: invalid from_tier '{from_tier}'; valid: {valid_tiers}"
    if to_tier not in valid_tiers:
        return f"ERROR: invalid to_tier '{to_tier}'; valid: {valid_tiers}"
    if from_tier == to_tier:
        return f"ERROR: from_tier and to_tier must be different"

    if memory_manager is None:
        registry = get_registry()
        memory_manager = registry.memory_manager

    try:
        # Handle long_term source with query-based promotion
        if from_tier == LONG_TERM and (query or key):
            search_query = query or key
            entries = await memory_manager.search(
                GOAT_ROLE, search_query, limit=1, memory_type=LONG_TERM
            )
            if not entries:
                return f"ERROR: no entry found in long_term for query: {search_query!r}"
            entry = entries[0]
            # Promote using the found entry's key
            result = await memory_manager.promote(
                GOAT_ROLE,
                entry.key,
                from_type=from_tier,
                to_type=to_tier,
                keep_source=keep_source,
            )
            if result is None:
                return f"ERROR: promote failed for {entry.key!r}"
            return f"Promoted (via query): {entry.key}: {from_tier} → {to_tier}"

        # Standard key-based promotion for working/episodic
        result = await memory_manager.promote(
            GOAT_ROLE,
            key,
            from_type=from_tier,
            to_type=to_tier,
            keep_source=keep_source,
        )
        if result is None:
            return f"ERROR: key '{key}' not found in {from_tier}"
        return f"Promoted {key}: {from_tier} → {to_tier}"
    except Exception as exc:
        return format_memory_error("memory_promote", exc)


MEMORY_PROMOTE = ToolDefinition(
    name="memory_promote",
    description="Promote a memory entry between tiers. For long_term source, use query instead of key.",
    parameters={
        "type": "object",
        "required": ["from_tier", "to_tier"],
        "properties": {
            "key": {
                "type": "string",
                "description": "Memory key to promote (for working/episodic source).",
            },
            "from_tier": {
                "type": "string",
                "enum": ["working", "episodic", "long_term"],
                "description": "Source tier",
            },
            "to_tier": {
                "type": "string",
                "enum": ["working", "episodic", "long_term"],
                "description": "Destination tier",
            },
            "keep_source": {
                "type": "boolean",
                "description": "Keep source after promote (default: False)",
                "default": False,
            },
            "query": {
                "type": "string",
                "description": "Search query for long_term source (finds entry by content).",
            },
        },
    },
    handler=_promote_handler,
)