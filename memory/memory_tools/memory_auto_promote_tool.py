"""Auto-promote tool — trigger manual memory promotion.

Provides MEMORY_AUTO_PROMOTE ToolDefinition for supervisor to
trigger bulk promotion between memory tiers on request.
"""
from __future__ import annotations

import logging

log = logging.getLogger("goat2.memory.tools")

from typing import TYPE_CHECKING

from config.tiers import WORKING, EPISODIC, LONG_TERM
from memory.memory_tools.memory_helpers import format_memory_error, make_tool

if TYPE_CHECKING:
    from memory.shared.memory_manager import MemoryManager

__all__ = ["MEMORY_AUTO_PROMOTE"]


async def _auto_promote_handler(
    from_tier: str,
    to_tier: str,
    keep_source: bool = False,
    limit: int = 100,
    memory_manager: "MemoryManager | None" = None,
) -> str:
    """Promote all eligible entries from one tier to another.

    Args:
        from_tier: Source tier (working, episodic, long_term)
        to_tier: Destination tier (working, episodic, long_term)
        keep_source: Whether to keep source after promote (default: False)
        limit: Max entries to process (default: 100)
        memory_manager: Optional injected MemoryManager

    Returns:
        Result message with count of promoted entries
    """
    valid_tiers = (WORKING, EPISODIC, LONG_TERM)
    if from_tier not in valid_tiers:
        return f"ERROR: invalid from_tier '{from_tier}'; valid: {valid_tiers}"
    if to_tier not in valid_tiers:
        return f"ERROR: invalid to_tier '{to_tier}'; valid: {valid_tiers}"
    if from_tier == to_tier:
        return f"ERROR: from_tier and to_tier must be different"

    if memory_manager is None:
        from tools.registry_accessor import get_registry
        registry = get_registry()
        memory_manager = registry.memory_manager

    try:
        count = await memory_manager.promote_all(
            "user_session",
            from_type=from_tier,
            to_type=to_tier,
            keep_source=keep_source,
            limit=limit,
        )
        return f"Promoted {count}/{min(limit, 100)} entries: {from_tier} → {to_tier}"
    except Exception as exc:
        return format_memory_error("memory_auto_promote", exc)


MEMORY_AUTO_PROMOTE = make_tool(
    name="memory_auto_promote",
    description="Bulk promote all eligible entries between tiers.",
    parameters={
        "type": "object",
        "required": ["from_tier", "to_tier"],
        "properties": {
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
            "limit": {
                "type": "integer",
                "description": "Max entries to process (default: 100)",
                "default": 100,
            },
        },
    },
    handler=_auto_promote_handler,
)