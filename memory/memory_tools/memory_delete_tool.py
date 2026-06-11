"""Memory delete tool — remove entries from any memory tier.

Provides MEMORY_DELETE ToolDefinition for supervisor to delete
entries from working, episodic, or long-term memory tiers.

MEMORY ACCESS ARCHITECTURE:
===========================
- GOAT (supervisor): Full delete access to all tiers
- DAG agents: No delete access (restricted in tool handlers)
"""
from __future__ import annotations

import logging

log = logging.getLogger("goat2.memory.tools")

from typing import TYPE_CHECKING

from config.roles import GOAT_ROLE, SESSION_ROLE
from memory.memory_tools.memory_helpers import format_memory_error, validate_tier, ALL_TIERS, make_tool

if TYPE_CHECKING:
    from memory.shared.memory_manager import MemoryManager

__all__ = ["MEMORY_DELETE"]


async def _delete_handler(
    key: str,
    tier: str = "working",
    role: str = GOAT_ROLE,
    memory_manager: "MemoryManager | None" = None,
) -> str:
    """Delete a memory entry by exact key.

    MEMORY ACCESS:
    - GOAT supervisor: Full delete access to all tiers
    - DAG agents: Denied (returns error)

    Args:
        key: Exact memory key to delete
        tier: Source tier (default: 'working')
        role: Caller role (GOAT_ROLE or SESSION_ROLE)
        memory_manager: Optional injected MemoryManager

    Returns:
        Success message or error string
    """
    # Restrict DAG agents from delete operations
    if role != GOAT_ROLE:
        return "ERROR: memory_delete is not available to DAG agents"

    error = validate_tier(tier, ALL_TIERS)
    if error:
        return error

    if memory_manager is None:
        from tools.registry_accessor import get_registry
        registry = get_registry()
        memory_manager = registry.memory_manager

    try:
        deleted = await memory_manager.delete(
            GOAT_ROLE, key, memory_type=tier
        )
        if deleted:
            return f"Deleted {key!r} from {tier}"
        return f"Key not found: {key!r}"
    except Exception as exc:
        return format_memory_error("memory_delete", exc)


MEMORY_DELETE = make_tool(
    name="memory_delete",
    description="Delete a memory entry by exact key from a specified tier.",
    parameters={
        "type": "object",
        "required": ["key"],
        "properties": {
            "key": {
                "type": "string",
                "description": "Exact memory key to delete.",
            },
            "tier": {
                "type": "string",
                "enum": list(ALL_TIERS),
                "description": "Source tier (default: 'working').",
                "default": "working",
            },
        },
    },
    handler=_delete_handler,
)