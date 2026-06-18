"""Memory last-write timestamp tool — check when each tier was last updated.

Provides a single ToolDefinition (MEMORY_LAST_WRITE) that queries the
shared working-memory backend (owned by the registry's MemoryManager) for
the last write timestamp of any memory tier (working, chromadb, letta).
The timestamp is automatically updated by the per-tier stores via
``memory.shared.last_write.sync_last_write``.

GOAT (supervisor) has full tier access with GOAT_ROLE from config.roles.
DAG agents are restricted to working tier only with SESSION_ROLE from config.roles.
"""

from __future__ import annotations

import logging
from typing import Final

from memory.memory_tools.memory_helpers import make_tool

log = logging.getLogger("goat2.memory.tools")

__all__ = ["MEMORY_LAST_WRITE"]

_ALLOWED_TIERS: Final[tuple[str, ...]] = ("working", "episodic", "long_term")

_SCHEMA = {
    "type": "object",
    "properties": {
        "tier": {
            "type": "string",
            "description": (
                "Memory tier to check: 'working', 'episodic', or 'long_term'. "
                "Returns ISO 8601 timestamp of last write to that tier."
            ),
            "enum": list(_ALLOWED_TIERS),
        },
    },
    "required": ["tier"],
}


async def _handler(tier: str) -> str:
    """Read the last-write timestamp of specified tier from the working backend.

    Returns ISO 8601 timestamp (or Unix float for the ``working`` tier
    written by ``MemoryCrudMixin.store``) or 'never' if no writes
    recorded. Returns ERROR: <reason> on failure.

    GOAT supervisor uses GOAT_ROLE for full access.
    DAG agents are restricted to working tier with SESSION_ROLE.
    """
    from memory.shared.last_write import read_last_write

    if tier not in _ALLOWED_TIERS:
        return f"ERROR: invalid tier {tier!r}. Allowed: {_ALLOWED_TIERS}"

    try:
        timestamp = await read_last_write(tier)
        if timestamp is None:
            return f"No writes recorded for {tier} tier yet."
        return f"Last write to {tier}: {timestamp}"
    except Exception as exc:
        return f"ERROR: failed to query last write: {exc}"


MEMORY_LAST_WRITE = make_tool(
    name="memory_last_write",
    description=(
        "Check the last write timestamp for a memory tier (working/episodic/long_term). "
        "Returns ISO 8601 timestamp from the working backend. Automatically updated on writes. "
        "GOAT has full tier access; DAG agents restricted to working tier only."
    ),
    parameters=_SCHEMA,
    handler=_handler,
)
