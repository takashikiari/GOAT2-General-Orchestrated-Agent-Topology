"""Central limits registry for GOAT 2.0 system constraints.

Resolution order: environment variable → hard-coded default.
Modular fallbacks (memory/dag/behavioral/tools) live in ``config.fallbacks``
and are re-exported below for backward compatibility with existing
callers that import them from ``config.limits``.
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
    # BUG-032: centralised defaults for cross-module limits.
    # Module-local _DEFAULT_* mirrors stay aligned with these
    # values — see docs/magic_numbers_policy.md and
    # tests/test_config_limits.py for the conformance tests.
    "DEFAULT_HISTORY_MAX_MESSAGES",
    "DEFAULT_PROMPT_MAX_ENTRIES",
    "DEFAULT_BACKGROUND_DRAIN_TIMEOUT_S",
    "DEFAULT_ERROR_MAX_CHARS",
    "DEFAULT_CORRECTIONS_LIMIT",
    "DEFAULT_TEMPORAL_FRESH_THRESHOLD_S",
    "DEFAULT_TEMPORAL_RECENT_THRESHOLD_S",
    "DEFAULT_TEMPORAL_DAY_THRESHOLD_S",
    # Faza 2: three-layer memory structure thresholds.
    "DEFAULT_TEMPORAL_PRESENT_MAX_AGE_S",
    "DEFAULT_TEMPORAL_PRESENT_PAST_MAX_AGE_S",
    "DEFAULT_TEMPORAL_PRESENT_MAX_ENTRIES",
    "DEFAULT_TEMPORAL_PRESENT_PAST_MAX_ENTRIES",
    "DEFAULT_TEMPORAL_PAST_MAX_ENTRIES",
    "DEFAULT_EPISODIC_TOP_K",
    # Modular fallbacks re-exported from config.fallbacks
    # (memory.toml / dag.toml / behavioral.toml / tools.toml).
    "WORKING_MAX_ENTRIES",
    "WORKING_WARN_THRESHOLD",
    "WORKING_FLUSH_ON_START",
    "WORKING_GC_INTERVAL_TURNS",
    "EPISODIC_MAX_ENTRIES",
    "EPISODIC_WARN_THRESHOLD",
    "EPISODIC_PROMOTE_THRESHOLD",
    "EPISODIC_DROP_THRESHOLD",
    "DAEMON_INTERVAL_S",
    "DAEMON_TIER1_AGE_HOURS",
    "DAEMON_TIER2_AGE_HOURS",
    "DAG_MAX_WAVES",
    "CRITIC_RERUNS_MAX",
    "UPSTREAM_REEXEC_TIMEOUT_S",
    "CRITIC_RERUN_TIMEOUT_S",
    "DAG_AUTO_CLEAN_DELAY_S",
    "LEARN_MIN_TURNS",
    "LEARN_MAX_TURNS",
    "CORRECTION_SENSITIVITY",
    "HUMOR_THRESHOLD",
    "FORMALITY_THRESHOLD",
    "DIRECTNESS_THRESHOLD",
    "VERBOSITY_DEFAULT",
    "HOT_RELOAD_INTERVAL_S",
    "SHELL_DEFAULT_TIMEOUT_S",
    "WEB_SEARCH_DEFAULT_RESULTS",
]

MAX_LINES_PER_FILE: Final[int] = 200
"""Maximum lines displayed from file read operations."""

MAX_RECALL_LIMIT: Final[int] = 50
"""Maximum entries returned from memory recall queries."""

MAX_TURNS_HISTORY: Final[int] = 20
"""Maximum conversation turns retained in session history."""

DAG_RESULT_TTL: Final[int] = 3600
"""Time-to-live for DAG execution results in Redis (seconds)."""

WORKING_MEMORY_TTL: Final[int] = 3600
"""Default TTL for working memory entries (seconds)."""

INFERRED_MEMORY_TTL: Final[int] = 604800
"""TTL for inferred facts stored in ChromaDB (seconds, 7 days)."""

# ─────────────────────────────────────────────────────────────────────
# RELIABILITY — DAG timeouts, retries, health probes
# Resolution: env var → hard-coded default.
# ─────────────────────────────────────────────────────────────────────


def _env_int(name: str, default: int) -> int:
    """Read an int from the environment, fall back on parse error."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        log.warning("limits: %s=%r not an int — falling back to %d", name, raw, default)
        return default


def _env_float(name: str, default: float) -> float:
    """Read a float from the environment, fall back on parse error."""
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
"""Hard upper bound on an entire DAG run (seconds)."""

WAVE_TIMEOUT: Final[int] = _env_int("WAVE_TIMEOUT", 120)
"""Hard upper bound on a single wave execution (seconds)."""

TASK_TIMEOUT: Final[int] = _env_int("TASK_TIMEOUT", 30)
"""Hard upper bound on a single task run (seconds)."""

MAX_RETRIES: Final[int] = _env_int("MAX_RETRIES", 2)
"""Number of retries per task after the initial attempt."""

HEALTH_TIMEOUT: Final[float] = _env_float("HEALTH_TIMEOUT", 5.0)
"""Per-service ceiling for health probes (seconds)."""

SEARXNG_URL: Final[str] = _env_str("SEARXNG_URL", "http://localhost:7777")
"""Base URL for the local SearXNG instance."""

SEARXNG_TIMEOUT: Final[int] = _env_int("SEARXNG_TIMEOUT", 10)
"""HTTP request timeout for SearXNG search queries (seconds)."""

LOG_LEVEL: Final[str] = _env_str("LOG_LEVEL", "INFO")
"""Default log level for GOAT 2.0 subsystems."""

# ─────────────────────────────────────────────────────────────────────
# BUG-032: Centralised cross-module defaults.
#
# Module-local mirrors stay aligned with these values via
# tests/test_config_limits.py. Override at runtime by setting
# the corresponding env var (e.g. DEFAULT_HISTORY_MAX_MESSAGES)
# or by editing the canonical source here.
# ─────────────────────────────────────────────────────────────────────

DEFAULT_HISTORY_MAX_MESSAGES: Final[int] = 200
"""ConversationHistory in-memory cap. ~50 turns."""

DEFAULT_PROMPT_MAX_ENTRIES: Final[int] = 50
"""Working-memory entries rendered into the prompt block."""

DEFAULT_BACKGROUND_DRAIN_TIMEOUT_S: Final[float] = 5.0
"""Wall-clock budget for draining background tasks at shutdown."""

DEFAULT_ERROR_MAX_CHARS: Final[int] = 200
"""Cap on the error-message text in fallback replies."""

DEFAULT_CORRECTIONS_LIMIT: Final[int] = 3
"""Default cap on recall_corrections result size."""

DEFAULT_TEMPORAL_FRESH_THRESHOLD_S:  Final[int] = 60
DEFAULT_TEMPORAL_RECENT_THRESHOLD_S: Final[int] = 3600
DEFAULT_TEMPORAL_DAY_THRESHOLD_S:    Final[int] = 86400
"""Relative-age label thresholds (BUG-003)."""

# ─────────────────────────────────────────────────────────────────────
# Faza 2: three-layer memory structure thresholds.
#
# Module-local mirrors of these values live in
# supervisor.session.mem_inject and are pinned to the canonical
# source via tests/test_three_layer_memory.py.
# ─────────────────────────────────────────────────────────────────────

DEFAULT_TEMPORAL_PRESENT_MAX_AGE_S: Final[int] = 300
"""Max age (seconds) for [Present] layer (< fresh). 5 min."""

DEFAULT_TEMPORAL_PRESENT_PAST_MAX_AGE_S: Final[int] = 86400
"""Max age (seconds) for [Present-Past] layer. 24 h."""

DEFAULT_TEMPORAL_PRESENT_MAX_ENTRIES: Final[int] = 50
"""Hard cap for [Present] entries."""

DEFAULT_TEMPORAL_PRESENT_PAST_MAX_ENTRIES: Final[int] = 30
"""Hard cap for [Present-Past] entries (working + episodic)."""

DEFAULT_TEMPORAL_PAST_MAX_ENTRIES: Final[int] = 20
"""Hard cap for [Past] entries."""

DEFAULT_EPISODIC_TOP_K: Final[int] = 5
"""Number of episodic recall hits in [Present-Past]."""

# ─────────────────────────────────────────────────────────────────────
# MODULAR FALLBACKS (memory/dag/behavioral/tools) — re-exported
# The canonical home of these constants is ``config/fallbacks.py``,
# which is organised by the same modular sections as the four new
# toml files (``memory.toml`` / ``dag.toml`` / ``behavioral.toml`` /
# ``tools.toml``). This block re-exports them so existing call sites
# that import from ``config.limits`` keep working — the user-facing
# canonical import path for new code is ``config.fallbacks`` (or the
# module-local ``_load_toml`` helper that each consuming module
# now uses to read its own section).
# ─────────────────────────────────────────────────────────────────────
from config.fallbacks import (  # noqa: E402,F401 — re-exports
    WORKING_MAX_ENTRIES,
    WORKING_WARN_THRESHOLD,
    WORKING_FLUSH_ON_START,
    WORKING_GC_INTERVAL_TURNS,
    EPISODIC_MAX_ENTRIES,
    EPISODIC_WARN_THRESHOLD,
    EPISODIC_PROMOTE_THRESHOLD,
    EPISODIC_DROP_THRESHOLD,
    DAEMON_INTERVAL_S,
    DAEMON_TIER1_AGE_HOURS,
    DAEMON_TIER2_AGE_HOURS,
    DAG_MAX_WAVES,
    CRITIC_RERUNS_MAX,
    UPSTREAM_REEXEC_TIMEOUT_S,
    CRITIC_RERUN_TIMEOUT_S,
    DAG_AUTO_CLEAN_DELAY_S,
    LEARN_MIN_TURNS,
    LEARN_MAX_TURNS,
    CORRECTION_SENSITIVITY,
    HUMOR_THRESHOLD,
    FORMALITY_THRESHOLD,
    DIRECTNESS_THRESHOLD,
    VERBOSITY_DEFAULT,
    HOT_RELOAD_INTERVAL_S,
    SHELL_DEFAULT_TIMEOUT_S,
    WEB_SEARCH_DEFAULT_RESULTS,
)
