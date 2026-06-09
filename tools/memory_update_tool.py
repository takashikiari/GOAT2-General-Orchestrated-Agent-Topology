"""Memory update tool — update or upsert entries in memory tiers.

Provides MEMORY_UPDATE ToolDefinition for supervisor to update
existing entries or create new ones if they don't exist (upsert).

MEMORY ACCESS ARCHITECTURE:
===========================
- GOAT (supervisor): Full update access to all tiers
- DAG agents: Working tier only with SESSION_ROLE

BEHAVIOR:
=========
- If key exists: updates content (episodic/long_term) or overwrites (working)
- If key doesn't exist: creates new entry (upsert behavior)
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from agents.base_agent import ToolDefinition
from config.roles import GOAT_ROLE
from memory.validation import sanitize_content, validate_memory_write
from tools.memory_helpers import (
    ALL_TIERS,
    format_memory_error,
    format_no_results,
    validate_tier,
)
from tools.registry_accessor import get_registry

if TYPE_CHECKING:
    from memory.memory_manager import MemoryManager

__all__ = ["MEMORY_UPDATE"]


async def _update_handler(
    key: str,
    value: str,
    tier: str = "working",
    memory_manager: "MemoryManager | None" = None,
) -> str:
    """Update an existing entry or create if not exists (upsert).

    MEMORY ACCESS:
    - GOAT supervisor: Full update access to all tiers
    - DAG agents: Working tier only

    Args:
        key: Memory key to update or create
        value: New content value
        tier: Target tier (default: 'working')
        memory_manager: Optional injected MemoryManager

    Returns:
        Success message indicating update or create
    """
    error = validate_tier(tier, ALL_TIERS)
    if error:
        return error

    # Validate and sanitize content
    try:
        validate_memory_write(key, value, tier)
        value = sanitize_content(value)
    except ValueError as exc:
        return f"ERROR: validation failed: {exc}"

    if memory_manager is None:
        registry = get_registry()
        memory_manager = registry.memory_manager

    # Check if entry exists
    existing = await memory_manager.locate(GOAT_ROLE, key, memory_type=tier)
    action = "Updated" if existing else "Created"

    try:
        await memory_manager.store(GOAT_ROLE, key, value, memory_type=tier)
        return f"{action} {key!r} in {tier}"
    except Exception as exc:
        return format_memory_error("memory_update", exc)


MEMORY_UPDATE = ToolDefinition(
    name="memory_update",
    description="Update an existing memory entry or create if not exists (upsert).",
    parameters={
        "type": "object",
        "required": ["key", "value"],
        "properties": {
            "key": {
                "type": "string",
                "description": "Memory key to update or create.",
            },
            "value": {
                "type": "string",
                "description": "New content value.",
            },
            "tier": {
                "type": "string",
                "enum": list(ALL_TIERS),
                "description": "Target tier (default: 'working').",
                "default": "working",
            },
        },
    },
    handler=_update_handler,
)