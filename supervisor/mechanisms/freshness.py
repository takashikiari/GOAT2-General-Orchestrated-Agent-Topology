"""Freshness scoring — bucket an entry's age into FRESH/RECENT/OLD.

Pure Python, no LLM, no I/O. Thresholds come from
``config/memory.toml [freshness]`` via ``config.modular_loader``.
Three labels, configurable cutoffs, no hardcoded numbers in
this file (defaults are a defensive fallback only).

USAGE:
    from supervisor.mechanisms.freshness import (
        score_freshness, FreshnessLabel, load_freshness_config,
    )

    label = score_freshness(entry, now=time.time())
    # Returns one of: "FRESH", "RECENT", "OLD"

LABEL SEMANTICS:
    FRESH  → < fresh_max_seconds   (entry is very recent; trust high)
    RECENT → < recent_max_seconds  (entry is recent; trust medium)
    OLD    → else                  (entry is stale; verify before use)

A missing or unparseable ``created_at_ts`` is treated as OLD
(safest default — unknown age is treated as the oldest possible).
"""
from __future__ import annotations

import logging
from typing import Final

log = logging.getLogger("goat2.supervisor.mechanisms.freshness")

__all__ = ["FreshnessLabel", "score_freshness", "load_freshness_config"]

# Frozen strings — GOAT's system prompt and behavior mechanisms
# pattern-match on these exact tokens.
FRESH:  Final[str] = "FRESH"
RECENT: Final[str] = "RECENT"
OLD:    Final[str] = "OLD"
FreshnessLabel = Final[str]  # type alias for callers

# Defensive fallback if memory.toml is missing or [freshness] is
# absent. The codebase owner tunes the real values; this is the
# safety net that keeps the mechanism functional in any environment.
_DEFAULTS: Final[dict[str, float]] = {
    "fresh_max_seconds":   300.0,   # 5 min
    "recent_max_seconds":  3600.0,  # 60 min
    "dag_max_age_seconds": 600.0,   # 10 min
}


def load_freshness_config() -> dict[str, float]:
    """Read [freshness] from config/memory.toml with fallback to defaults.

    Resolution order: toml > module default. The toml loader is
    non-fatal — a missing file silently falls back to defaults so
    the mechanism remains usable in any environment.

    Returns:
        dict with three float keys: ``fresh_max_seconds``,
        ``recent_max_seconds``, ``dag_max_age_seconds``.
    """
    cfg: dict[str, float] = dict(_DEFAULTS)
    try:
        from config.modular_loader import load_memory_config
        section = (load_memory_config() or {}).get("freshness", {}) or {}
        for key in _DEFAULTS:
            raw = section.get(key)
            if raw is None:
                continue
            try:
                cfg[key] = float(raw)
            except (TypeError, ValueError):
                log.debug("freshness: %s=%r not numeric — using default", key, raw)
    except Exception as exc:  # noqa: BLE001 — config load is best-effort
        log.debug("freshness: memory.toml [freshness] load skipped: %s", exc)
    return cfg


# Loaded once at import time. Pure read of a static toml file.
_CFG: Final[dict[str, float]] = load_freshness_config()


def score_freshness(entry: dict, now: float) -> str:
    """Return ``"FRESH" | "RECENT" | "OLD"`` for ``entry`` at time ``now``.

    Args:
        entry: A memory record with at least ``created_at_ts`` (float
            seconds since epoch). Other fields are ignored.
        now: Reference time in seconds since epoch (usually
            ``time.time()``).

    Returns:
        ``"FRESH"`` when age < fresh_max_seconds, ``"RECENT"`` when
        age < recent_max_seconds, else ``"OLD"``. Missing /
        unparseable ``created_at_ts`` → ``"OLD"`` (safest).
    """
    ts = entry.get("created_at_ts") if isinstance(entry, dict) else None
    try:
        age = now - float(ts)
    except (TypeError, ValueError):
        return OLD
    if age < _CFG["fresh_max_seconds"]:
        return FRESH
    if age < _CFG["recent_max_seconds"]:
        return RECENT
    return OLD
