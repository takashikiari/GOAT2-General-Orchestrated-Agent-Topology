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

from typing import Final

from supervisor.mechanisms.freshness import score_freshness
from supervisor.mechanisms.namespace import classify_namespace

__all__ = ["MAX_ENTRIES", "build_context"]

# Hard cap on lines rendered into the prompt. Bounds prompt
# growth across long sessions; the most recent MAX_ENTRIES
# records (after sort) win.
MAX_ENTRIES: Final[int] = 50

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


def _format_line(record: dict, now: float) -> str:
    """Render one working-memory line: ``- [FRESH][CONV] key: preview``."""
    if not isinstance(record, dict):
        return ""
    key   = record.get("key", "?")
    src   = classify_namespace(key)
    fresh = score_freshness(record, now)
    return f"- [{fresh}][{src}] {key}: {_preview(record)}"


def build_context(entries: list[dict], intent: str, now: float) -> str:
    """Assemble the ``[Working Memory]`` block from raw records.

    Args:
        entries: List of working-memory records (dicts with at
            least ``key`` and ``created_at_ts``).
        intent: The raw user intent for this turn. Reserved for
            future intent-aware filtering (DAG staleness uses
            ``staleness.is_stale`` directly). Accepted here for
            forward-compat.
        now: Reference time in seconds since epoch.

    Returns:
        A single string starting with ``"[Working Memory]\\n"``,
        sorted trust-high first. Returns ``""`` when no entries
        survive the sort + cap.
    """
    _ = intent  # reserved — staleness is handled in caller
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
    lines = [_format_line(r, now) for r in ordered if _format_line(r, now)]
    if not lines:
        return ""
    return "[Working Memory]\n" + "\n".join(lines)
