"""Central limits registry for GOAT 2.0 system constraints.

This module defines all numeric limits and thresholds used throughout
GOAT 2.0 for memory operations, file handling, and conversation management.

LIMIT CATEGORIES:
=================
FILE LIMITS:
    - MAX_LINES_PER_FILE: Maximum lines displayed from file reads
    - Prevents overwhelming LLM context with large files

MEMORY LIMITS:
    - MAX_RECALL_LIMIT: Maximum entries returned from memory queries
    - Balances recall quality with context window constraints

CONVERSATION LIMITS:
    - MAX_TURNS_HISTORY: Maximum conversation turns retained
    - Prevents unbounded memory growth

TTL VALUES:
    - DAG_RESULT_TTL: Time-to-live for DAG execution results in Redis
    - WORKING_MEMORY_TTL: Default TTL for working memory entries
    - INFERRED_MEMORY_TTL: TTL for inferred facts in ChromaDB (7 days)

All files should import from this module instead of hardcoding numeric values.
"""
from __future__ import annotations

import logging
from typing import Final

log = logging.getLogger("goat2.config.limits")

__all__ = [
    "MAX_LINES_PER_FILE",
    "MAX_RECALL_LIMIT",
    "MAX_TURNS_HISTORY",
    "DAG_RESULT_TTL",
    "WORKING_MEMORY_TTL",
    "INFERRED_MEMORY_TTL",
]

MAX_LINES_PER_FILE: Final[int] = 200
"""Maximum lines displayed from file read operations.

Prevents overwhelming LLM context window with large files.
File tools truncate output to this limit.
"""

MAX_RECALL_LIMIT: Final[int] = 50
"""Maximum entries returned from memory recall queries.

Balances recall quality with context window constraints.
Higher values may cause token overflow in LLM prompts.
"""

MAX_TURNS_HISTORY: Final[int] = 20
"""Maximum conversation turns retained in session history.

Prevents unbounded memory growth during long conversations.
Older turns are summarized or discarded.
"""

DAG_RESULT_TTL: Final[int] = 3600
"""Time-to-live for DAG execution results in Redis (seconds).

DAG results stored with key format dag_result:<session_id>.
1 hour TTL ensures results available for validation but
prevents indefinite accumulation.
"""

WORKING_MEMORY_TTL: Final[int] = 3600
"""Default TTL for working memory entries (seconds).

Applied to all entries stored in WORKING tier unless
explicitly overridden. 1 hour default balances session
persistence with memory cleanup.
"""

INFERRED_MEMORY_TTL: Final[int] = 604800
"""TTL for inferred facts stored in ChromaDB (seconds).

7 days (604800 seconds) provides temporary storage for
inferred facts that may become relevant. Longer-lived
than working memory but expires to prevent accumulation.
"""
