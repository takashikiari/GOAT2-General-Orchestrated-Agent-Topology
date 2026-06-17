"""Modular config fallback constants for memory/dag/behavioral/tools.

This module holds the **Python fallback** values for every constant that
lives in one of the four new modular toml files (``memory.toml``,
``dag.toml``, ``behavioral.toml``, ``tools.toml``). The toml file is
the canonical source of truth at runtime; these constants exist so
the modules stay runnable when the toml is absent (tests, minimal
installs).

The legacy limits (TTL values, env-driven timeouts, etc.) live in
``config/limits.py`` and continue to be re-exported from there for
backward compatibility — but **new** constants added as part of the
modular config split go here, in dedicated sections, so each value
sits next to its documentation and a single import covers a whole
domain.

Each constant is annotated with the matching toml key and the
section/filename that owns it. No singletons; no I/O; pure data.
"""
from __future__ import annotations

from typing import Final

__all__ = [
    # memory.toml — [working]
    "WORKING_MAX_ENTRIES",
    "WORKING_WARN_THRESHOLD",
    "WORKING_FLUSH_ON_START",
    "WORKING_GC_INTERVAL_TURNS",
    # memory.toml — [episodic]
    "EPISODIC_MAX_ENTRIES",
    "EPISODIC_WARN_THRESHOLD",
    "EPISODIC_PROMOTE_THRESHOLD",
    "EPISODIC_DROP_THRESHOLD",
    # memory.toml — [daemon]
    "DAEMON_INTERVAL_S",
    "DAEMON_TIER1_AGE_HOURS",
    "DAEMON_TIER2_AGE_HOURS",
    # dag.toml — [execution]
    "DAG_MAX_WAVES",
    "CRITIC_RERUNS_MAX",
    "UPSTREAM_REEXEC_TIMEOUT_S",
    "CRITIC_RERUN_TIMEOUT_S",
    "DAG_AUTO_CLEAN_DELAY_S",
    # behavioral.toml — [learning]
    "LEARN_MIN_TURNS",
    "LEARN_MAX_TURNS",
    "CORRECTION_SENSITIVITY",
    # behavioral.toml — [style]
    "HUMOR_THRESHOLD",
    "FORMALITY_THRESHOLD",
    "DIRECTNESS_THRESHOLD",
    "VERBOSITY_DEFAULT",
    # tools.toml — [hot_reload] / [shell] / [web_search]
    "HOT_RELOAD_INTERVAL_S",
    "SHELL_DEFAULT_TIMEOUT_S",
    "WEB_SEARCH_DEFAULT_RESULTS",
]

# ─────────────────────────────────────────────────────────────────────
# memory.toml — [working]  (read by memory/working/capacity.py)
# ─────────────────────────────────────────────────────────────────────

WORKING_MAX_ENTRIES: Final[int] = 100
"""Working memory hard cap (entries per agent_role)."""

WORKING_WARN_THRESHOLD: Final[int] = 85
"""Working memory soft warning threshold (log WARNING at this count)."""

WORKING_FLUSH_ON_START: Final[bool] = True
"""Schedule a working-memory flush on session start."""

WORKING_GC_INTERVAL_TURNS: Final[int] = 10
"""Tick the GC every N turns."""

# ─────────────────────────────────────────────────────────────────────
# memory.toml — [episodic]  (read by memory/episodic/sliding_window.py)
# ─────────────────────────────────────────────────────────────────────

EPISODIC_MAX_ENTRIES: Final[int] = 300
"""Episodic memory hard cap (entries per agent_role)."""

EPISODIC_WARN_THRESHOLD: Final[int] = 250
"""Episodic memory soft warning threshold."""

EPISODIC_PROMOTE_THRESHOLD: Final[float] = 0.5
"""Score >= this → promote (working → episodic)."""

EPISODIC_DROP_THRESHOLD: Final[float] = 0.3
"""Score < this → drop (delete from working)."""

# ─────────────────────────────────────────────────────────────────────
# memory.toml — [daemon]  (read by memory/shared/memory_daemon.py)
# ─────────────────────────────────────────────────────────────────────

DAEMON_INTERVAL_S: Final[float] = 60.0
"""MemoryDaemon sweep interval (seconds)."""

DAEMON_TIER1_AGE_HOURS: Final[float] = 24.0
"""Only promote working entries older than this (tier 1)."""

DAEMON_TIER2_AGE_HOURS: Final[float] = 48.0
"""Episodic sliding-window age threshold (hours, tier 2)."""

# ─────────────────────────────────────────────────────────────────────
# dag.toml — [execution]  (read by supervisor/pipeline/workflow.py)
# ─────────────────────────────────────────────────────────────────────

DAG_MAX_WAVES: Final[int] = 10
"""Hard cap on the number of topological waves per DAG."""

CRITIC_RERUNS_MAX: Final[int] = 1
"""Maximum re-executions per critic task (critic fallback loop)."""

UPSTREAM_REEXEC_TIMEOUT_S: Final[float] = 30.0
"""Per-upstream-task re-execution timeout (seconds)."""

CRITIC_RERUN_TIMEOUT_S: Final[float] = 30.0
"""Per-critic re-run timeout (seconds)."""

DAG_AUTO_CLEAN_DELAY_S: Final[float] = 60.0
"""Delay before auto-deleting ``dag:*`` keys after a DAG finishes."""

# ─────────────────────────────────────────────────────────────────────
# behavioral.toml — [learning]  (read by supervisor/behavior/behavior_analyzer.py)
# ─────────────────────────────────────────────────────────────────────

LEARN_MIN_TURNS: Final[int] = 2
"""Minimum user turns before style inference runs."""

LEARN_MAX_TURNS: Final[int] = 20
"""Maximum recent turns analyzed for style scoring."""

CORRECTION_SENSITIVITY: Final[float] = 0.8
"""0.0 = ignore corrections, 1.0 = strict."""

# ─────────────────────────────────────────────────────────────────────
# behavioral.toml — [style]  (read by supervisor/behavior/behavior_analyzer.py)
# ─────────────────────────────────────────────────────────────────────

HUMOR_THRESHOLD: Final[float] = 0.3
"""Minimum humor-signal density for ``humor: playful``."""

FORMALITY_THRESHOLD: Final[float] = 0.5
"""Politeness-vs-slang signal split point."""

DIRECTNESS_THRESHOLD: Final[float] = 0.7
"""Curt-word density for ``tone: direct``."""

VERBOSITY_DEFAULT: Final[str] = "moderate"
"""Default verbosity bucket when the user has not signalled one."""

# ─────────────────────────────────────────────────────────────────────
# tools.toml — [hot_reload] / [shell] / [web_search]
# ─────────────────────────────────────────────────────────────────────

HOT_RELOAD_INTERVAL_S: Final[float] = 30.0
"""Polling cadence for the tools watcher (seconds)."""

SHELL_DEFAULT_TIMEOUT_S: Final[float] = 30.0
"""asyncio.wait_for ceiling for shell.run."""

WEB_SEARCH_DEFAULT_RESULTS: Final[int] = 5
"""Default max snippets returned by web_search."""
