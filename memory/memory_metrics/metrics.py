"""Memory health metrics — basic statistics for memory tiers.

Provides simple metrics functions to check the health and usage of
memory tiers. Used for monitoring and debugging.

EXPORTS:
- count_working_entries(mm): Number of entries in working memory
- count_episodic_entries(mm): Number of entries in episodic memory
- count_long_term_entries(mm): Number of entries in long-term memory
- memory_health_report(mm): Dict with tier status and counts
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from memory.shared.memory_manager import MemoryManager

log = logging.getLogger("goat2.memory.metrics")

__all__ = [
    "count_working_entries",
    "count_episodic_entries",
    "count_long_term_entries",
    "memory_health_report",
]


async def count_working_entries(mm: "MemoryManager") -> int:
    """Count entries in working memory tier.

    Args:
        mm: MemoryManager instance

    Returns:
        Number of entries in working memory
    """
    try:
        entries = await mm.working.list("goat", limit=1000)
        count = len(entries)
        log.debug("count_working_entries: %d", count)
        return count
    except Exception as exc:
        log.warning("count_working_entries: failed: %s", exc)
        return 0


async def count_episodic_entries(mm: "MemoryManager") -> int:
    """Count entries in episodic memory tier.

    Args:
        mm: MemoryManager instance

    Returns:
        Number of entries in episodic memory
    """
    try:
        entries = await mm.episodic.list("goat", limit=1000)
        count = len(entries)
        log.debug("count_episodic_entries: %d", count)
        return count
    except Exception as exc:
        log.warning("count_episodic_entries: failed: %s", exc)
        return 0


async def count_long_term_entries(mm: "MemoryManager") -> int:
    """Count entries in long-term memory tier.

    Note: Letta doesn't have a simple count API. This returns the
    number of blocks that can be retrieved.

    Args:
        mm: MemoryManager instance

    Returns:
        Number of entries in long-term memory
    """
    try:
        # Letta lists agents, not entries directly
        # Return 0 as placeholder - requires Letta API for accurate count
        log.debug("count_long_term_entries: 0 (Letta has no count API)")
        return 0
    except Exception as exc:
        log.warning("count_long_term_entries: failed: %s", exc)
        return 0


async def memory_health_report(mm: "MemoryManager") -> dict:
    """Get comprehensive health report for all memory tiers.

    Args:
        mm: MemoryManager instance

    Returns:
        Dict with:
        - status: LayerStatus for each tier
        - counts: entry counts per tier
        - healthy: bool indicating overall health
    """
    # Get status
    status = await mm.status()

    # Get counts
    working_count = await count_working_entries(mm)
    episodic_count = await count_episodic_entries(mm)
    long_term_count = await count_long_term_entries(mm)

    report = {
        "status": {
            "working": status.working,
            "episodic": status.episodic,
            "long_term": status.long_term,
        },
        "counts": {
            "working": working_count,
            "episodic": episodic_count,
            "long_term": long_term_count,
        },
        "healthy": status.working or status.episodic,
    }
    log.debug("memory_health_report: healthy=%s", report["healthy"])
    return report