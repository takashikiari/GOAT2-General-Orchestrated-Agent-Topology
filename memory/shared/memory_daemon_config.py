"""Memory daemon — toml loading helpers.

Pure functions that read sections from ``config/memory.toml`` and
return typed defaults. Extracted from ``memory_daemon.py`` to keep
the daemon's main file under the 260-line ceiling.

The daemon imports these once at startup; the values become module
constants that the daemon's ``__init__`` defaults reference.
"""
from __future__ import annotations

from typing import Any

from config.fallbacks import (
    DAEMON_INTERVAL_S,
    DAEMON_TIER1_AGE_HOURS,
    DAEMON_TIER2_AGE_HOURS,
    EPISODIC_MAX_ENTRIES,
    EPISODIC_WARN_THRESHOLD,
    WORKING_MAX_ENTRIES,
    WORKING_WARN_THRESHOLD,
)
from config.modular_loader import load_memory_config

__all__ = [
    "MEMORY_DAEMON_DEFAULTS",
]

_daemon_cfg = load_memory_config().get("daemon", {})
_working_cfg = load_memory_config().get("working", {})
_episodic_cfg = load_memory_config().get("episodic", {})


def _cfg_int(section: dict, key: str, default: int) -> int:
    """Read an int from a toml section; fall back to ``default`` on error."""
    raw = section.get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _cfg_float(section: dict, key: str, default: float) -> float:
    """Read a float from a toml section; fall back on error."""
    raw = section.get(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


# Resolved at import time. Dict shape matches ``MemoryDaemon.__init__`` kwargs
# (one extra key ``tier1_age_hours`` for clarity; converted to seconds below).
MEMORY_DAEMON_DEFAULTS: dict[str, Any] = {
    "interval_s":     _cfg_float(_daemon_cfg, "interval_seconds", DAEMON_INTERVAL_S),
    "tier1_age_hours": _cfg_float(_daemon_cfg, "tier1_age_hours", DAEMON_TIER1_AGE_HOURS),
    "tier2_age_hours": _cfg_float(_daemon_cfg, "tier2_age_hours", DAEMON_TIER2_AGE_HOURS),
    "working_soft":   _cfg_int(_working_cfg, "warn_threshold", WORKING_WARN_THRESHOLD),
    "working_max":    _cfg_int(_working_cfg, "max_entries", WORKING_MAX_ENTRIES),
    "episodic_soft":  _cfg_int(_episodic_cfg, "warn_threshold", EPISODIC_WARN_THRESHOLD),
    "episodic_max":   _cfg_int(_episodic_cfg, "max_entries", EPISODIC_MAX_ENTRIES),
}
