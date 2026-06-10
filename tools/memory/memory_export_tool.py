"""Memory export tool — bulk dump entries from memory tiers.

Provides MEMORY_EXPORT ToolDefinition for supervisor to export
all entries from a specified memory tier as JSON.

MEMORY ACCESS ARCHITECTURE:
===========================
- GOAT (supervisor): Full access to all tiers
- DAG agents: Working tier only

OUTPUT:
=======
Returns JSON array with entry objects containing:
- key: entry key
- content: entry content
- timestamp: ISO 8601 creation timestamp
- metadata: additional metadata (if any)

LIMIT:
======
Maximum 1000 entries per export to prevent huge outputs.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from agents.base_agent import ToolDefinition
from config.roles import GOAT_ROLE
from tools.memory.memory_helpers import format_memory_error, validate_tier, ANY_TIERS
from tools.registry_accessor import get_registry

if TYPE_CHECKING:
    from memory.memory_manager import MemoryManager

__all__ = ["MEMORY_EXPORT"]

MAX_EXPORT_LIMIT = 1000


async def _export_handler(
    tier: str = "working",
    limit: int = 100,
    memory_manager: "MemoryManager | None" = None,
) -> str:
    """Export entries from a memory tier as JSON.

    MEMORY ACCESS:
    - GOAT supervisor: Full access to all tiers
    - DAG agents: Working tier only

    Args:
        tier: Source tier to export from
        limit: Max entries (default 100, max 1000)
        memory_manager: Optional injected MemoryManager

    Returns:
        JSON string with exported entries, or error
    """
    error = validate_tier(tier, ANY_TIERS)
    if error:
        return error

    # Cap limit
    limit = min(limit, MAX_EXPORT_LIMIT)

    if memory_manager is None:
        registry = get_registry()
        memory_manager = registry.memory_manager

    try:
        entries = await memory_manager.list(GOAT_ROLE, memory_type=tier, limit=limit)

        if not entries:
            return json.dumps({"tier": tier, "entries": []}, indent=2)

        export_data = {
            "tier": tier,
            "count": len(entries),
            "entries": [
                {
                    "key": e.key,
                    "content": e.content,
                    "timestamp": e.metadata.get("created_at", ""),
                    "source": e.source,
                }
                for e in entries
            ],
        }

        return json.dumps(export_data, indent=2, ensure_ascii=False)
    except Exception as exc:
        return format_memory_error("memory_export", exc)


MEMORY_EXPORT = ToolDefinition(
    name="memory_export",
    description="Export entries from a memory tier as JSON.",
    parameters={
        "type": "object",
        "required": [],
        "properties": {
            "tier": {
                "type": "string",
                "enum": list(ANY_TIERS),
                "description": "Source tier to export from (default: 'working').",
                "default": "working",
            },
            "limit": {
                "type": "integer",
                "description": "Max entries to export (default: 100, max: 1000).",
                "default": 100,
            },
        },
    },
    handler=_export_handler,
)