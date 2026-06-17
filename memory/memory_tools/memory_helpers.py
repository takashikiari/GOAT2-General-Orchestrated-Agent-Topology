"""Memory tool helpers — shared utilities for memory CRUD operations.

Extracts common logic used by memory_tools.py and
memory_temporal_tools.py. GOAT supervisor uses GOAT_ROLE for full tier
access; DAG agents use SESSION_ROLE and are restricted to the working
tier. Provides role/tier constants, error/entry formatting, validation,
the ToolDefinition factory, plus the Letta normaliser and timeout
wrappers added when GOAT's read of long-term memory was fixed.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Final

from config.roles import GOAT_ROLE, SESSION_ROLE
from config.tiers import WORKING, EPISODIC, LONG_TERM, ANY

if TYPE_CHECKING:
    from agents.base_agent import ToolDefinition
    from memory.shared.memory_manager import MemoryManager

log = logging.getLogger("goat2.memory.tools")

__all__ = [
    "GOAT_ROLE", "SESSION_ROLE",
    "ALL_TIERS", "ANY_TIERS", "SEARCH_TIERS",
    "role_for_tier", "format_memory_error", "format_entries",
    "format_no_results", "validate_tier", "make_tool",
    "LETA_CALL_TIMEOUT_S", "normalize_tier",
    "letta_search_safe", "letta_list_safe",
]

# ---------------------------------------------------------------------------
# Tier constants — define valid memory tier identifiers
# ---------------------------------------------------------------------------

ALL_TIERS: Final[tuple[str, ...]] = (WORKING, EPISODIC, LONG_TERM)
"""All three memory tiers for write operations.

Tier descriptions:
- working: Session-scoped, TTL-enforced, fastest
- episodic: ChromaDB semantic search, persistent
- long_term: Letta core-memory blocks, most persistent
"""

ANY_TIERS: Final[tuple[str, ...]] = (ANY,) + ALL_TIERS
"""Valid tier values for search/read operations (includes 'any').

The 'any' tier searches across all available tiers and merges results.
"""

# Search/read tier set with the user-facing aliases the tool layer accepts.
# The handler normalises them via normalize_tier() before they reach
# MemoryManager; the JSON-schema enum and validate_tier() both use this set
# so the tool never rejects "letta" or "all" at the door.
SEARCH_TIERS: Final[tuple[str, ...]] = ANY_TIERS + ("letta", "all")

# ---------------------------------------------------------------------------
# Role helpers
# ---------------------------------------------------------------------------


def role_for_tier(tier: str) -> str:
    """Return the correct agent_role for a given memory tier.

    - "long_term" / "letta"  → GOAT_ROLE  (Letta stores agent-level data)
    - everything else         → SESSION_ROLE (working/episodic store session data)
    """
    return GOAT_ROLE if tier in ("long_term", "letta") else SESSION_ROLE


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def format_memory_error(operation: str, exc: Exception) -> str:
    """Format a memory operation error with consistent prefix.

    Args:
        operation: The operation that failed (e.g., 'memory_search', 'store').
        exc: The exception that was raised.

    Returns:
        Formatted error string: "ERROR: {operation} failed: {exc}"

    Example:
        >>> format_memory_error("memory_search", ValueError("not found"))
        'ERROR: memory_search failed: not found'
    """
    return f"ERROR: {operation} failed: {exc}"


def format_entries(entries: list, max_content_len: int = 200) -> str:
    """Format memory entries as a readable string.

    Args:
        entries: List of MemoryEntry objects.
        max_content_len: Maximum characters to show from content.

    Returns:
        Newline-separated string with format: "[{source}] {key}: {content}"

    Example:
        >>> format_entries([MemoryEntry(source="file", key="test", content="hello")])
        '[file] test: hello'
    """
    if not entries:
        return ""
    return "\n".join(
        f"[{e.source}] {e.key}: {e.content[:max_content_len]}"
        for e in entries
    )


def format_no_results(context: str = "") -> str:
    """Format a 'no results' message with optional context.

    Args:
        context: Additional context (e.g., time range, query).

    Returns:
        Human-friendly message indicating no entries found.

    Example:
        >>> format_no_results("for: 'test query'")
        "No entries found for: 'test query'."
    """
    if context:
        return f"No entries found {context}."
    return "No entries found."


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def validate_tier(tier: str, allowed: tuple[str, ...]) -> str | None:
    """Validate that a tier value is in the allowed set.

    Args:
        tier: The tier value to validate.
        allowed: Tuple of allowed tier values.

    Returns:
        None if valid, or error message string if invalid.

    Example:
        >>> validate_tier(WORKING, ANY_TIERS)
        None
        >>> validate_tier("invalid", ANY_TIERS)
        "ERROR: invalid tier 'invalid'; valid: ('any', 'working', 'episodic', 'long_term')"
    """
    if tier not in allowed:
        return f"ERROR: invalid tier '{tier}'; valid: {allowed}"
    return None


# ---------------------------------------------------------------------------
# ToolDefinition factory
# ---------------------------------------------------------------------------


def make_tool(
    name: str,
    description: str,
    parameters: dict[str, Any],
    handler: Any,
) -> "ToolDefinition":
    """Build a ToolDefinition with a lazy import of ``agents.base_agent``.

    The import is performed inside this helper so that ``memory_tools``
    files keep ``agents`` out of their module-level imports — this avoids
    any module-load-time coupling between the memory layer and the
    agent base class, even though the dependency is one-way and
    cycle-free at runtime.

    Args:
        name: Tool identifier.
        description: Human-readable tool description.
        parameters: JSON-Schema-style parameter definition.
        handler: Async or sync callable invoked by the supervisor.

    Returns:
        A ``ToolDefinition`` instance.

    Example:
        >>> MEMORY_SEARCH = make_tool("memory_search", "...", {...}, _handler)
    """
    from agents.base_agent import ToolDefinition
    return ToolDefinition(
        name=name,
        description=description,
        parameters=parameters,
        handler=handler,
    )


# ---------------------------------------------------------------------------
# Letta routing helpers — GOAT can read long-term memory (PROBLEM fix)
# ---------------------------------------------------------------------------
#
# Before this section, tool handlers (memory_search, memory_recent,
# memory_direct_query, memory_count) accepted tier="letta" but then
# passed the raw string to MemoryManager.search(memory_type=...),
# which calls MemoryType(tier). MemoryType only knows "working" /
# "episodic" / "long_term", so "letta" raised ValueError. The helpers
# below normalise the tier name and wrap Letta calls in a 10 s ceiling.
# ---------------------------------------------------------------------------


LETA_CALL_TIMEOUT_S: float = 10.0
"""Per-call ceiling for any Letta operation surfaced through a tool (seconds)."""


def normalize_tier(tier: str) -> str:
    """Translate user-facing tier aliases to internal MemoryType values.

    Mapping:
        "letta" -> "long_term"   (Letta IS the long_term backend)
        "all"   -> "any"         (fan-out trigger in MemoryManager.search)
        anything else (working / episodic / long_term / any) -> unchanged
    """
    if tier == "letta":
        return "long_term"
    if tier == "all":
        return "any"
    return tier


async def letta_search_safe(mm: "MemoryManager", query: str, limit: int) -> list:
    """Search Letta with a hard 10 s ceiling. Never raises."""
    try:
        return await asyncio.wait_for(
            mm.long_term.search(GOAT_ROLE, query, limit=limit),
            timeout=LETA_CALL_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        log.warning("memory_helpers: letta search timed out after %.1fs", LETA_CALL_TIMEOUT_S)
        return []
    except Exception as exc:  # noqa: BLE001 — tool wrapper must never raise
        log.warning("memory_helpers: letta search failed: %s", exc)
        return []


async def letta_list_safe(mm: "MemoryManager", limit: int) -> list:
    """List Letta entries with a hard 10 s ceiling. Never raises."""
    try:
        return await asyncio.wait_for(
            mm.long_term.list(GOAT_ROLE, limit=limit),
            timeout=LETA_CALL_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        log.warning("memory_helpers: letta list timed out after %.1fs", LETA_CALL_TIMEOUT_S)
        return []
    except Exception as exc:  # noqa: BLE001
        log.warning("memory_helpers: letta list failed: %s", exc)
        return []
