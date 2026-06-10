"""Memory metrics — health statistics and monitoring utilities.

Provides functions to check the health and usage of memory tiers.
Used for monitoring, debugging, and system diagnostics.

EXPORTS:
- count_working_entries(mm): Number of entries in working memory
- count_episodic_entries(mm): Number of entries in episodic memory
- count_long_term_entries(mm): Number of entries in long-term memory
- memory_health_report(mm): Dict with tier status and counts
"""

from memory.memory_metrics.metrics import (
    count_episodic_entries,
    count_long_term_entries,
    count_working_entries,
    memory_health_report,
)

__all__ = [
    "count_working_entries",
    "count_episodic_entries",
    "count_long_term_entries",
    "memory_health_report",
]