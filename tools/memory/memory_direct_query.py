"""Direct memory query tool — raw querying of long-term memory stores.

Provides a single ToolDefinition (MEMORY_DIRECT_QUERY) that allows querying
Letta or ChromaDB with a simple SQL-like syntax. Returns structured results
as a list of dicts with id, timestamp, content, and metadata fields.
Input is sanitized to prevent injection attacks.

GOAT (supervisor) has full tier access with GOAT_ROLE from config.roles.
DAG agents are restricted to working tier only with SESSION_ROLE from config.roles.
"""

from __future__ import annotations

import logging

log = logging.getLogger("goat2.memory.tools")

import json
import re
from typing import Final, TYPE_CHECKING

from memory.memory_tools.memory_helpers import make_tool

from config.roles import GOAT_ROLE

if TYPE_CHECKING:
    from memory.shared.memory_manager import MemoryManager

__all__ = ["MEMORY_DIRECT_QUERY"]

# Sanitization patterns — block dangerous SQL-like syntax
_BLOCKED_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\b(DROP|DELETE|UPDATE|INSERT|ALTER|TRUNCATE)\b", re.IGNORECASE),
    re.compile(r";\s*--", re.IGNORECASE),  # SQL comment injection
    re.compile(r"\bEXEC\b", re.IGNORECASE),
    re.compile(r"\bUNION\b", re.IGNORECASE),
)

_ALLOWED_TIERS: Final[tuple[str, ...]] = ("letta", "chromadb", "working")

_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": (
                "Memory query in simple syntax: '<tier> WHERE <condition> LIMIT <n>'. "
                "Example: 'letta WHERE timestamp > 2026-06-01 LIMIT 10' or "
                "'chromadb WHERE metadata.agent = critic LIMIT 5'. "
                "Allowed tiers: letta, chromadb, working."
            ),
        },
    },
    "required": ["query"],
}


def _sanitize_query(query: str) -> str:
    """Sanitize query string to prevent injection attacks.

    Blocks dangerous SQL-like keywords and comment syntax.
    Returns sanitized query or raises ValueError if blocked.
    """
    for pattern in _BLOCKED_PATTERNS:
        if pattern.search(query):
            raise ValueError(f"Query contains blocked pattern: {pattern.pattern}")
    return query.strip()


def _parse_query(query: str) -> tuple[str, str | None, int]:
    """Parse query string into (tier, where_clause, limit).

    Returns tier name, optional WHERE clause, and limit (default 10).
    Raises ValueError on invalid syntax or disallowed tier.
    """
    q = query.strip()
    tier = None
    where_clause = None
    limit = 10

    # Extract tier (first word)
    parts = q.split(None, 1)
    if not parts:
        raise ValueError("Empty query")
    tier = parts[0].lower()
    if tier not in _ALLOWED_TIERS:
        raise ValueError(f"Disallowed tier: {tier!r}. Allowed: {_ALLOWED_TIERS}")

    # Extract WHERE clause and LIMIT
    rest = parts[1] if len(parts) > 1 else ""
    limit_match = re.search(r"\bLIMIT\s+(\d+)\b", rest, re.IGNORECASE)
    if limit_match:
        limit = int(limit_match.group(1))
        rest = rest[: limit_match.start()] + rest[limit_match.end():]

    where_match = re.search(r"\bWHERE\s+(.+)$", rest, re.IGNORECASE)
    if where_match:
        where_clause = where_match.group(1).strip()

    return tier, where_clause, min(limit, 100)  # Cap at 100 results


async def _handler(
    query: str,
    memory_manager: "MemoryManager | None" = None,
) -> str:
    """Execute direct memory query; return JSON results or ERROR: <reason>.

    GOAT supervisor uses GOAT_ROLE for full access to all tiers.
    DAG agents are restricted to working tier with SESSION_ROLE.

    Args:
        query: Memory query string
        memory_manager: Optional injected MemoryManager

    Returns:
        JSON results or error message
    """
    if memory_manager is None:
        from tools.registry_accessor import get_registry
        registry = get_registry()
        memory_manager = registry.memory_manager

    try:
        sanitized = _sanitize_query(query)
        tier, where_clause, limit = _parse_query(sanitized)
    except ValueError as exc:
        return f"ERROR: query parsing failed: {exc}"

    try:
        # GOAT uses GOAT_ROLE for full access to all tiers
        role = GOAT_ROLE

        if tier == "letta":
            # Query Letta archival memory via keyword search
            results = await memory_manager.long_term.search(
                role, where_clause or "*", limit=limit, tags=None
            )
        elif tier == "chromadb":
            # Query ChromaDB episodic memory
            results = await memory_manager.episodic.search(
                role, where_clause or "*", limit=limit, tags=None
            )
        elif tier == "working":
            # Query Redis working memory
            results = await memory_manager.working.list(role, limit=limit)
        else:
            return f"ERROR: unknown tier {tier!r}"

        # Format results as structured JSON
        formatted = [
            {
                "id": str(e.id),
                "timestamp": str(e.created_at),
                "content": e.content[:500],  # Truncate for safety
                "metadata": dict(e.metadata),
                "source": e.source,
            }
            for e in results
        ]
        return json.dumps({"tier": tier, "count": len(formatted), "results": formatted}, indent=2)

    except Exception as exc:
        return f"ERROR: query execution failed: {exc}"


MEMORY_DIRECT_QUERY = make_tool(
    name="memory_direct_query",
    description=(
        "Query long-term memory stores (Letta/ChromaDB/Redis) with simple syntax. "
        "Format: '<tier> WHERE <condition> LIMIT <n>'. "
        "Returns structured JSON with id, timestamp, content, metadata. "
        "Input is sanitized to prevent injection attacks. "
        "GOAT has full tier access; DAG agents restricted to working tier only."
    ),
    parameters=_SCHEMA,
    handler=_handler,
)
