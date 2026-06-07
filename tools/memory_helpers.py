"""Memory tool helpers — shared utilities for memory CRUD operations.

This module extracts common logic used by memory_tools.py and
memory_temporal_tools.py to keep those files under 200 lines.

MEMORY ACCESS ARCHITECTURE:
===========================
- GOAT (supervisor): Full tier access with role="goat"
- DAG agents: Working tier only with role="user_session"
- Validation: Tier restrictions enforced in tool handlers

Provides:
- Role and tier constants for memory access control
- Error formatting helpers
- Entry formatting for consistent output
- Validation helpers for memory operations

TOOL WIRING:
============
All memory tools accept an optional 'role' parameter:
- Default: GOAT_ROLE ("goat") for supervisor
- DAG agents: DAG_AGENT_ROLE ("user_session")
- Tier restrictions automatically enforced based on role
"""
from __future__ import annotations

from typing import Final

__all__ = [
    "GOAT_ROLE",
    "DAG_AGENT_ROLE",
    "ALL_TIERS",
    "ANY_TIERS",
    "format_memory_error",
    "format_entries",
    "format_no_results",
    "validate_tier",
]

# ---------------------------------------------------------------------------
# Role constants — control memory tier access
# ---------------------------------------------------------------------------

GOAT_ROLE: Final[str] = "goat"
"""Supervisor role with full access to all memory tiers.

GOAT can read/write to:
- WORKING (Redis): Session-scoped with TTL
- EPISODIC (ChromaDB): Semantic search storage
- LONG_TERM (Letta): Core memory blocks
"""

DAG_AGENT_ROLE: Final[str] = "user_session"
"""DAG agent role restricted to working tier only.

DAG agents can only:
- Read/write WORKING memory (Redis)
- Cannot access EPISODIC or LONG_TERM tiers
- Prevents memory pollution from agent operations
"""

# ---------------------------------------------------------------------------
# Tier constants — define valid memory tier identifiers
# ---------------------------------------------------------------------------

ALL_TIERS: Final[tuple[str, ...]] = ("working", "episodic", "long_term")
"""All three memory tiers for write operations.

Tier descriptions:
- working: Session-scoped, TTL-enforced, fastest
- episodic: ChromaDB semantic search, persistent
- long_term: Letta core-memory blocks, most persistent
"""

ANY_TIERS: Final[tuple[str, ...]] = ("any",) + ALL_TIERS
"""Valid tier values for search/read operations (includes 'any').

The 'any' tier searches across all available tiers and merges results.
"""

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
        >>> validate_tier("working", ANY_TIERS)
        None
        >>> validate_tier("invalid", ANY_TIERS)
        "ERROR: invalid tier 'invalid'; valid: ('any', 'working', 'episodic', 'long_term')"
    """
    if tier not in allowed:
        return f"ERROR: invalid tier '{tier}'; valid: {allowed}"
    return None
