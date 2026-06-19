"""Working-memory context builder — assemble the labeled
``[FRESH][CONV] key: preview`` block that GOAT's system prompt
references.

Pure Python, no LLM. Composes the other mechanisms
(``freshness`` + ``namespace``) into a single render. Each
working-memory record becomes one line:

    - [FRESHNESS][NAMESPACE] key: preview

Sorted trust-high first: (CONV+FRESH) > (GOAT+FRESH) > (SYS+...)
> (DAG+OLD). Within a bucket, newer first.

USAGE:
    from supervisor.mechanisms.context_builder import build_context

    block = build_context(entries, intent, now=time.time())
    # Returns "[Working Memory]\n- [FRESH][CONV] turn:abc: hi\n..."
    # Returns "" when there are no admissible entries.
"""
from __future__ import annotations

import logging
from typing import Final

from supervisor.mechanisms.freshness import score_freshness
from supervisor.mechanisms.namespace import classify_namespace
from supervisor.mechanisms.staleness import STALE_PREFIX, is_stale

__all__ = ["build_context", "load_max_entries"]

log = logging.getLogger("goat2.supervisor.mechanisms.context_builder")

# Hard default — used when memory.toml is missing or [working]
# is absent. Operators tune the real value; this is the safety
# net that keeps the mechanism functional in any environment.
_DEFAULT_MAX_ENTRIES: Final[int] = 50


def load_max_entries() -> int:
    """Read ``max_prompt_entries`` from config/memory.toml [working].

    Returns the configured cap on lines rendered into the
    prompt, or ``_DEFAULT_MAX_ENTRIES`` when the file / section
    is missing. Cached at import time.
    """
    try:
        from config.modular_loader import load_memory_config
        section = (load_memory_config() or {}).get("working", {}) or {}
        raw = section.get("max_prompt_entries")
        if raw is not None:
            return int(raw)
    except (TypeError, ValueError):
        log.debug("context_builder: max_prompt_entries not int — using default")
    except Exception as exc:  # noqa: BLE001
        log.debug("context_builder: max_prompt_entries load skipped: %s", exc)
    return _DEFAULT_MAX_ENTRIES


MAX_ENTRIES: Final[int] = load_max_entries()

# Source-rank order — CONV is most trusted, DAG is least.
# Used as the primary sort key.
_SOURCE_RANK: Final[dict[str, int]] = {
    "CONV": 0,
    "GOAT": 1,
    "SYS":  2,
    "DAG":  3,
}

# Freshness rank — FRESH > RECENT > OLD.
_FRESHNESS_RANK: Final[dict[str, int]] = {
    "FRESH":  0,
    "RECENT": 1,
    "OLD":    2,
}


def _preview(record: dict) -> str:
    """Render a record's content as a one-line preview (no newlines)."""
    content = (record.get("content") or "") if isinstance(record, dict) else ""
    return " ".join(content.split())  # split() collapses all whitespace


def _sort_key(record: dict, now: float) -> tuple[int, int, float]:
    """Trust-high first: (source_rank, freshness_rank, age)."""
    if not isinstance(record, dict):
        return (99, 99, 0.0)
    src = classify_namespace(record.get("key", ""))
    fr  = score_freshness(record, now)
    try:
        age = max(0.0, now - float(record.get("created_at_ts") or 0.0))
    except (TypeError, ValueError):
        age = float("inf")
    return (
        _SOURCE_RANK.get(src, 99),
        _FRESHNESS_RANK.get(fr, 99),
        age,
    )


def _format_line(record: dict, now: float, intent: str = "") -> str:
    """Render one working-memory line: ``- [FRESH][CONV] key: preview``.

    DAG entries older than ``dag_max_age_seconds`` (or whose intent
    doesn't mention a DAG-related keyword) are rendered with the
    ``[STALE]`` prefix so the LLM sees them as potentially expired.
    Non-DAG entries never get the prefix — staleness only applies
    to DAG namespaced records.

    Args:
        record: Working-memory record (dict with ``key`` and
            ``created_at_ts``).
        now: Reference time in seconds since epoch.
        intent: The raw user intent for this turn. Passed through to
            ``is_stale`` so an old DAG entry can be un-flagged when
            the user is explicitly asking about DAG state.

    Returns:
        A single line string, or ``""`` when the record is not a dict.
    """
    if not isinstance(record, dict):
        return ""
    key   = record.get("key", "?")
    src   = classify_namespace(key)
    fresh = score_freshness(record, now)
    stale_mark = f"{STALE_PREFIX} " if is_stale(record, intent or "", now) else ""
    return f"{stale_mark}- [{fresh}][{src}] {key}: {_preview(record)}"


def build_context(entries: list[dict], intent: str, now: float) -> str:
    """Assemble the ``[Working Memory]`` block from raw records.

    The ``intent`` is forwarded to ``_format_line`` so old DAG entries
    are correctly flagged ``[STALE]`` (or kept when the user is
    explicitly asking about DAG state).

    Args:
        entries: List of working-memory records (dicts with at
            least ``key`` and ``created_at_ts``).
        intent: The raw user intent for this turn.
        now: Reference time in seconds since epoch.

    Returns:
        A single string starting with ``"[Working Memory]\\n"``,
        sorted trust-high first. Returns ``""`` when no entries
        survive the sort + cap.
    """
    if not entries:
        return ""
    # Sort first, cap second.
    ordered = sorted(
        (e for e in entries if isinstance(e, dict)),
        key=lambda r: _sort_key(r, now),
    )
    ordered = ordered[:MAX_ENTRIES]
    if not ordered:
        return ""
    lines = [_format_line(r, now, intent) for r in ordered if _format_line(r, now, intent)]
    if not lines:
        return ""
    return "[Working Memory]\n" + "\n".join(lines)
