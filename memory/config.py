"""Memory system configuration constants.

Central registry for memory-related constants used across the memory module.
Replaces hardcoded values with configurable constants.
"""
from __future__ import annotations

import logging

log = logging.getLogger("goat2.memory.config")

__all__ = [
    "WORKING_BACKEND",
    "EPISODIC_BACKEND",
    "LONG_TERM_BACKEND",
    "PROMOTION_TURN_EPISODIC",
    "PROMOTION_TURN_LONG_TERM",
    "POLLUTION_GUARD_MIN_LENGTH",
]

# Memory backends
WORKING_BACKEND: str = "redis"
"""Backend used for working memory tier."""

EPISODIC_BACKEND: str = "chromadb"
"""Backend used for episodic memory tier."""

LONG_TERM_BACKEND: str = "letta"
"""Backend used for long-term memory tier."""

# Promotion thresholds (in conversation turns)
PROMOTION_TURN_EPISODIC: int = 2
"""Turn threshold for promoting working → episodic memory.

When turn_count >= 4 (messages), working memory entries are promoted
to episodic storage.
"""

PROMOTION_TURN_LONG_TERM: int = 3
"""Turn threshold for promoting episodic → long-term memory.

When turn_count >= 6 (messages), episodic memory entries are promoted
to long-term storage.
"""

# Quality guard
POLLUTION_GUARD_MIN_LENGTH: int = 10
"""Minimum content length for PollutionGuard validation.

Content shorter than this is flagged for review before promotion
to higher tiers.
"""