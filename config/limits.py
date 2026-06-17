"""Central limits registry for GOAT 2.0 system constraints.

This module defines all numeric limits and thresholds used throughout
GOAT 2.0 for memory operations, file handling, conversation management,
and DAG execution reliability (timeouts + retries + health checks).

LIMIT CATEGORIES:
=================
FILE LIMITS:
    - MAX_LINES_PER_FILE: Maximum lines displayed from file reads

MEMORY LIMITS:
    - MAX_RECALL_LIMIT: Maximum entries returned from memory queries

CONVERSATION LIMITS:
    - MAX_TURNS_HISTORY: Maximum conversation turns retained

TTL VALUES:
    - DAG_RESULT_TTL: Time-to-live for DAG execution results in Redis
    - WORKING_MEMORY_TTL: Default TTL for working memory entries
    - INFERRED_MEMORY_TTL: TTL for inferred facts in ChromaDB (7 days)

RELIABILITY (timeouts + retries + health):
    - DAG_TIMEOUT:    Hard upper bound on an entire DAG run (seconds)
    - WAVE_TIMEOUT:   Hard upper bound on a single wave execution
    - TASK_TIMEOUT:   Hard upper bound on a single task run
    - MAX_RETRIES:    Retry budget per task (initial + retries)
    - HEALTH_TIMEOUT: Per-service ceiling for health probes
    - SEARXNG_URL / SEARXNG_TIMEOUT: web search service endpoint + timeout

LOGGING:
    - LOG_LEVEL: Default log level (env-driven, default INFO)

All files should import from this module instead of hardcoding numeric values.
Resolution order is: environment variable → hard-coded default.
"""
from __future__ import annotations

import logging
import os
from typing import Final

log = logging.getLogger("goat2.config.limits")

__all__ = [
    "MAX_LINES_PER_FILE",
    "MAX_RECALL_LIMIT",
    "MAX_TURNS_HISTORY",
    "DAG_RESULT_TTL",
    "WORKING_MEMORY_TTL",
    "INFERRED_MEMORY_TTL",
    "DAG_TIMEOUT",
    "WAVE_TIMEOUT",
    "TASK_TIMEOUT",
    "MAX_RETRIES",
    "HEALTH_TIMEOUT",
    "SEARXNG_URL",
    "SEARXNG_TIMEOUT",
    "LOG_LEVEL",
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

# ─────────────────────────────────────────────────────────────────────
# RELIABILITY — DAG timeouts, retries, health probes
# Resolution: env var → hard-coded default (no toml coupling here so
# limits.py stays a leaf module with no internal config imports).
# ─────────────────────────────────────────────────────────────────────


def _env_int(name: str, default: int) -> int:
    """Read an int from the environment, fall back to ``default`` on any parse error."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        log.warning("limits: %s=%r not an int — falling back to %d", name, raw, default)
        return default


def _env_float(name: str, default: float) -> float:
    """Read a float from the environment, fall back to ``default`` on any parse error."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        log.warning("limits: %s=%r not a float — falling back to %.1f", name, raw, default)
        return default


def _env_str(name: str, default: str) -> str:
    """Read a string from the environment; empty string treated as unset."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw


DAG_TIMEOUT: Final[int] = _env_int("DAG_TIMEOUT", 300)
"""Hard upper bound on an entire DAG run (seconds).

Prevents a single user intent from blocking the supervisor
forever. The DAG is killed with whatever partial results it
had after this many seconds.
"""

WAVE_TIMEOUT: Final[int] = _env_int("WAVE_TIMEOUT", 120)
"""Hard upper bound on a single wave execution (seconds).

A wave is a group of tasks with no inter-dependencies that
run concurrently. If the gather() does not complete within
this many seconds, the wave is aborted and the DAG continues.
"""

TASK_TIMEOUT: Final[int] = _env_int("TASK_TIMEOUT", 30)
"""Hard upper bound on a single task run (seconds).

Each task is wrapped in ``asyncio.wait_for(runner(...),
timeout=TASK_TIMEOUT)``. On timeout the task is marked failed
with ``error="timeout:<n>s"`` and downstream tasks are skipped.
"""

MAX_RETRIES: Final[int] = _env_int("MAX_RETRIES", 2)
"""Number of retries per task after the initial attempt.

Total attempts per task = 1 + MAX_RETRIES (default 3).
Re-exported by ``supervisor.pipeline.task_retry`` for backward
compatibility — that module used to define its own constant.
"""

HEALTH_TIMEOUT: Final[float] = _env_float("HEALTH_TIMEOUT", 5.0)
"""Per-service ceiling for health probes (seconds).

Used by ``supervisor.health.health_check`` — each service
(Redis, ChromaDB, Letta, SearXNG) has at most this long to
respond before the probe reports DOWN.
"""

SEARXNG_URL: Final[str] = _env_str("SEARXNG_URL", "http://localhost:7777")
"""Base URL for the local SearXNG instance.

Used by ``supervisor.health`` for liveness probing and by
``tools.web.web_search`` for query routing.
"""

SEARXNG_TIMEOUT: Final[int] = _env_int("SEARXNG_TIMEOUT", 10)
"""HTTP request timeout for SearXNG search queries (seconds).

Applied to both the search tool and the health probe.
"""

LOG_LEVEL: Final[str] = _env_str("LOG_LEVEL", "INFO")
"""Default log level for GOAT 2.0 subsystems.

Override with the ``LOG_LEVEL`` env var. Valid values follow
the ``logging`` module: ``DEBUG``, ``INFO``, ``WARNING``,
``ERROR``, ``CRITICAL``.
"""
