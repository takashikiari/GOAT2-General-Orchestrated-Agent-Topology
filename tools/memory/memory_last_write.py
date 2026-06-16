"""Memory last-write timestamp tool — check when each tier was last updated.

Provides a single ToolDefinition (MEMORY_LAST_WRITE) that queries Redis for
the last write timestamp of any memory tier (working, chromadb, letta).
The timestamp is automatically updated by the ChromaDB write wrapper on each store.

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
    """Query Redis for last write timestamp of specified tier.

    Returns ISO 8601 timestamp or 'never' if no writes recorded.
    Returns ERROR: <reason> on failure.
    
    GOAT supervisor uses GOAT_ROLE for full access.
    DAG agents are restricted to working tier with SESSION_ROLE.
    """
    from memory.working.redis_backend import RedisBackend

    if tier not in _ALLOWED_TIERS:
        return f"ERROR: invalid tier {tier!r}. Allowed: {_ALLOWED_TIERS}"

    try:
        redis = RedisBackend()
        r = await redis._get_redis()
        key = f"goat2:working:last_write:{tier}"
        timestamp = await r.get(key)  # type: ignore[union-attr]
        await redis.close()

        if timestamp is None:
            return f"No writes recorded for {tier} tier yet."
        return f"Last write to {tier}: {timestamp}"

    except Exception as exc:
        return f"ERROR: failed to query last write: {exc}"


MEMORY_LAST_WRITE = make_tool(
    name="memory_last_write",
    description=(
        "Check the last write timestamp for a memory tier (working/episodic/long_term). "
        "Returns ISO 8601 timestamp from Redis. Automatically updated on writes. "
        "GOAT has full tier access; DAG agents restricted to working tier only."
    ),
    parameters=_SCHEMA,
    handler=_handler,
)
