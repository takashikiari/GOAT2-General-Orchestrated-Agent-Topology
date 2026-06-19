"""Temporal formatting — render working-memory records with relative age.

Pure Python, no LLM, no I/O. Reads thresholds from
``config/memory.toml [temporal]`` with a defensive fallback. The format
emitted here is what the GOAT LLM actually sees — entries missing a
``created_at_ts`` are explicitly labelled ``[unknown age]`` rather than
silently presented as fresh.

USAGE:
    from memory.temporal.temporal_format import (
        relative_age_label, format_entries_with_age, load_temporal_config,
    )

    line = relative_age_label(entry.created_at_ts, now=time.time(), cfg=cfg)
    block = format_entries_with_age(entries, max_content_len=200, now=now, cfg=cfg)

LABEL SEMANTICS:
    [Ns ago]   → seconds (config.fresh_threshold_s default 60)
    [Nm ago]   → minutes
    [Nh ago]   → hours
    [Nd ago]   → days (config.day_threshold_s default 86400)
    [unknown age] → entry.created_at_ts is 0 / missing / unparseable
"""
from __future__ import annotations

import logging
from typing import Final

log = logging.getLogger("goat2.memory.temporal.temporal_format")

__all__ = [
    "DEFAULT_FRESH_THRESHOLD_S",
    "DEFAULT_RECENT_THRESHOLD_S",
    "DEFAULT_DAY_THRESHOLD_S",
    "UNKNOWN_AGE_LABEL",
    "load_temporal_config",
    "relative_age_label",
    "format_entries_with_age",
]


# Defensive defaults — operator tunes the real values in
# config/memory.toml [temporal]. Used only when the section is missing.
DEFAULT_FRESH_THRESHOLD_S:  Final[int] = 60
DEFAULT_RECENT_THRESHOLD_S: Final[int] = 3600
DEFAULT_DAY_THRESHOLD_S:    Final[int] = 86400

UNKNOWN_AGE_LABEL: Final[str] = "[unknown age]"


def load_temporal_config() -> dict[str, float | int | bool]:
    """Read [temporal] from config/memory.toml with safe defaults.

    Resolution order: toml > module default. The toml loader is
    non-fatal — a missing file silently falls back to defaults so the
    formatter remains usable in any environment.

    Returns:
        dict with keys ``show_relative_age`` (bool),
        ``fresh_threshold_s`` (int), ``recent_threshold_s`` (int),
        ``day_threshold_s`` (int).
    """
    out: dict[str, float | int | bool] = {
        "show_relative_age":   True,
        "fresh_threshold_s":   DEFAULT_FRESH_THRESHOLD_S,
        "recent_threshold_s":  DEFAULT_RECENT_THRESHOLD_S,
        "day_threshold_s":     DEFAULT_DAY_THRESHOLD_S,
    }
    try:
        from config.modular_loader import load_memory_config
        section = (load_memory_config() or {}).get("temporal", {}) or {}
        bool_raw = section.get("show_relative_age")
        if isinstance(bool_raw, bool):
            out["show_relative_age"] = bool_raw
        for key in ("fresh_threshold_s", "recent_threshold_s", "day_threshold_s"):
            raw = section.get(key)
            if raw is None:
                continue
            try:
                out[key] = int(raw)
            except (TypeError, ValueError):
                log.debug("temporal_format: %s=%r not int — using default", key, raw)
    except Exception as exc:  # noqa: BLE001 — config load is best-effort
        log.debug("temporal_format: memory.toml [temporal] load skipped: %s", exc)
    return out


# Loaded once at import time; pure read of a static toml file.
_CFG: Final[dict[str, float | int | bool]] = load_temporal_config()


def relative_age_label(
    ts: float | int | None,
    *,
    now: float,
    cfg: dict[str, float | int | bool] | None = None,
) -> str:
    """Render a relative-age prefix for one entry's timestamp.

    Args:
        ts: Epoch seconds for the entry's ``created_at_ts``. Values
            that are 0, missing, or unparseable return ``[unknown age]``
            rather than fabricating a label.
        now: Reference time in epoch seconds.
        cfg: Optional pre-loaded config (uses module default when None).

    Returns:
        One of ``[Ns ago]``, ``[Nm ago]``, ``[Nh ago]``, ``[Nd ago]``,
        or ``[unknown age]``.
    """
    if cfg is None:
        cfg = _CFG
    try:
        ts_f = float(ts)
    except (TypeError, ValueError):
        return UNKNOWN_AGE_LABEL
    if ts_f <= 0.0:
        return UNKNOWN_AGE_LABEL
    age = max(0.0, now - ts_f)
    fresh  = int(cfg.get("fresh_threshold_s",  DEFAULT_FRESH_THRESHOLD_S))
    recent = int(cfg.get("recent_threshold_s", DEFAULT_RECENT_THRESHOLD_S))
    day    = int(cfg.get("day_threshold_s",    DEFAULT_DAY_THRESHOLD_S))
    if age < fresh:
        return f"[{int(age)}s ago]"
    if age < recent:
        return f"[{int(age / 60)}m ago]"
    if age < day:
        return f"[{int(age / 3600)}h ago]"
    return f"[{int(age / day)}d ago]"


def format_entries_with_age(
    entries: list,
    *,
    max_content_len: int = 200,
    now: float,
    cfg: dict[str, float | int | bool] | None = None,
) -> str:
    """Format memory entries with relative-age prefix.

    Output format:
        ``[age] [source] key: content``

    Args:
        entries: Iterable of objects exposing ``.source`` (str), ``.key``
            (str), ``.content`` (str), and a timestamp accessor. Entries
            that don't expose ``.metadata.get("created_at_ts")`` are
            rendered with ``[unknown age]``.
        max_content_len: Max characters of content per entry.
        now: Reference time for age computation.
        cfg: Optional pre-loaded temporal config.

    Returns:
        Newline-separated lines, or ``""`` for an empty input list.
    """
    if not entries:
        return ""
    if cfg is None:
        cfg = _CFG
    lines: list[str] = []
    for e in entries:
        if e is None:
            continue
        source = getattr(e, "source", "?")
        key    = getattr(e, "key", "?")
        content = (getattr(e, "content", "") or "")[:max_content_len]
        ts = 0.0
        meta = getattr(e, "metadata", None)
        if isinstance(meta, dict):
            try:
                ts = float(meta.get("created_at_ts") or 0.0)
            except (TypeError, ValueError):
                ts = 0.0
        age_label = relative_age_label(ts, now=now, cfg=cfg)
        lines.append(f"{age_label} [{source}] {key}: {content}")
    return "\n".join(lines)