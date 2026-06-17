"""Fan-out recall across all three memory tiers and concurrent info extraction per turn.

REGISTRY INJECTION (PHASE 4):
=============================
mem_turn() now requires `registry` parameter.
Passed to maybe_store_info() for settings access.

NAMESPACE SEPARATION:
=====================
Working-memory keys are partitioned by prefix. The context builder
applies a default filter so DAG coordination entries never leak
into conversational context:

  - dag:*    EXCLUDED by default. DAG status / results live in their
             own namespace; surfacing them here would pollute the
             conversational prompt. Use the dag-aware loaders
             (collect_finished, status) when DAG results are needed.
  - conv:*   ALWAYS included. Conversation turns and user signals.
  - live:*   ALWAYS included. Live verified data (health checks,
             runtime facts).
  - goat:*   ALWAYS included. GOAT state (pending DAG, routing).
  - anything else → included (unknown namespaces are treated as
    conversational until we know better).

The filter is a single prefix check, no regex. ``working_memory_block``
takes an optional ``include_dag`` flag for the few callers that need
the unfiltered view.

SOURCE + FRESHNESS SCORING:
===========================
Each working-memory line is prefixed with two short labels so GOAT can
decide trust level per entry:

  freshness: based on record age at read time
    - < 5  min  → [FRESH]
    - 5-60 min  → [RECENT]
    - > 60 min  → [OLD]

  source: derived from the key prefix
    - dag:*   → [DAG]
    - conv:*  → [CONV]
    - goat:*  → [GOAT]
    - live:*  → [LIVE]
    - other   → [OTHER]

Lines look like:  ``- [FRESH][CONV] key (2026-06-17 14:02): content``
The labels are stable strings; GOAT can pattern-match them cheaply.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Final

from config.roles import SESSION_ROLE

if TYPE_CHECKING:
    from memory.shared import MemoryManager
    from config.registry import Registry

from supervisor.behavior.info_extract import maybe_store_info

__all__ = ["mem_turn", "recall_context", "working_memory_block"]

_LIMIT: Final[int] = 5
_WM_LIMIT: Final[int] = 50
log = logging.getLogger("goat2.supervisor.session")

# Age buckets (seconds) for the freshness score.
_FRESH_MAX_AGE_S: Final[float] = 5 * 60      # < 5 min  → [FRESH]
_RECENT_MAX_AGE_S: Final[float] = 60 * 60    # < 60 min → [RECENT]; > → [OLD]

# Prefixes that are ALWAYS included in the conversational context block.
_CONVERSATIONAL_PREFIXES: Final[tuple[str, ...]] = ("conv:", "live:", "goat:")
# Prefixes that are NEVER included unless ``include_dag`` is True.
_DAG_PREFIX: Final[str] = "dag:"


def _is_dag_key(key: object) -> bool:
    """True when ``key`` is a DAG-namespaced working-memory key."""
    return isinstance(key, str) and key.startswith(_DAG_PREFIX)


def _is_conversational_key(key: object) -> bool:
    """True when ``key`` is a conversational / state prefix (conv/live/goat)."""
    return isinstance(key, str) and key.startswith(_CONVERSATIONAL_PREFIXES)


def _partition_keys(keys: list[str], *, include_dag: bool) -> tuple[list[str], list[str]]:
    """Split a key list into (kept, dag_dropped) by namespace prefix.

    Unknown prefixes are KEPT — they are treated as conversational
    until we know better. The dag bucket is reported separately so
    callers can log how many dag entries were filtered out.
    """
    kept: list[str] = []
    dag_dropped: list[str] = []
    for k in keys:
        if _is_dag_key(k):
            if include_dag:
                kept.append(k)
            else:
                dag_dropped.append(k)
        else:
            kept.append(k)
    return kept, dag_dropped


def _source_label(key: object) -> str:
    """Map a key prefix to a short source label GOAT can pattern-match."""
    if not isinstance(key, str):
        return "[OTHER]"
    if key.startswith(_DAG_PREFIX):
        return "[DAG]"
    if key.startswith("conv:"):
        return "[CONV]"
    if key.startswith("goat:"):
        return "[GOAT]"
    if key.startswith("live:"):
        return "[LIVE]"
    return "[OTHER]"


def _freshness_label(record: dict) -> str:
    """Bucket a record's age into [FRESH]/[RECENT]/[OLD]."""
    ts = record.get("created_at_ts")
    try:
        age = time.time() - float(ts)
    except (TypeError, ValueError):
        return "[OLD]"  # unknown / unparseable → treat as oldest (safest)
    if age < _FRESH_MAX_AGE_S:
        return "[FRESH]"
    if age < _RECENT_MAX_AGE_S:
        return "[RECENT]"
    return "[OLD]"


def _fmt_ts(record: dict) -> str:
    """Render a record's creation time as 'YYYY-MM-DD HH:MM' for display."""
    ts = record.get("created_at_ts")
    if ts:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    return str(record.get("created_at") or "")[:16]


def _format_line(record: dict) -> str:
    """One rendered working-memory line: ``- [FRESH][CONV] key (ts): content``."""
    key = record.get("key", "?")
    freshness = _freshness_label(record)
    source = _source_label(key)
    ts = _fmt_ts(record)
    content = (record.get("content") or "").strip().replace(chr(10), " ")
    return f"- {freshness}{source} {key} ({ts}): {content}"


async def working_memory_block(mm: MemoryManager | None, *, include_dag: bool = False) -> str:
    """Build a '[Working Memory]' block listing session entries with freshness + source scores.

    Loads every entry (up to _WM_LIMIT) directly from the working backend — no
    semantic-similarity filtering — so GOAT has complete session awareness. Each
    line is rendered as ``- [FRESHNESS][SOURCE] key (timestamp): content``,
    oldest first.

    By default, ``dag:*`` keys are EXCLUDED from the block — those are DAG
    coordination entries (status, results, control flags) and would pollute
    the conversational prompt. Pass ``include_dag=True`` for the few callers
    that need the unfiltered view. Returns '' on any failure or when empty.
    """
    if mm is None:
        return ""
    try:
        backend = mm.working.backend
        all_keys = list(await backend.keys(SESSION_ROLE))
        kept, dropped = _partition_keys(all_keys, include_dag=include_dag)
        if dropped:
            log.debug("working_memory_block: filtered %d dag:* keys (include_dag=False)", len(dropped))
        records: list[dict] = []
        for k in kept:
            rec = await backend.get(SESSION_ROLE, k)
            if rec:
                records.append(rec)
        records.sort(key=lambda r: float(r.get("created_at_ts") or 0.0))
        if not records:
            return ""
        lines = [_format_line(r) for r in records[:_WM_LIMIT]]
        log.debug("working_memory_block: %d entries (filtered %d dag)", len(lines), len(dropped))
        return "[Working Memory]\n" + "\n".join(lines)
    except Exception as exc:
        log.debug("working_memory_block failed: %s", exc)
        return ""


async def recall_context(mm: MemoryManager | None, query: str) -> str:
    """Return the cross-tier '[Memory]' fan-out PLUS the conversational '[Working Memory]' block.

    The fan-out (WORKING+EPISODIC+LONG_TERM semantic recall) is preserved for
    relevance; the working-memory block is appended with ``dag:*`` filtered
    out so DAG coordination entries never leak into the conversational
    prompt. Degrades to whichever block is available on error.
    """
    if mm is None:
        return "[Memory: UNAVAILABLE]"
    try:
        hits = await mm.recall(SESSION_ROLE, query, limit=_LIMIT)
        lines = [h.content.strip() for h in hits if h.content.strip()]
        mem_block = ("[Memory]\n" + "\n".join(f"- {ln}" for ln in lines)) if lines else ""
    except Exception as exc:
        log.error("recall_context fan-out failed: %s: %s", type(exc).__name__, exc)
        mem_block = ""
    wm_block = await working_memory_block(mm)  # dag:* filtered by default
    blocks = [b for b in (mem_block, wm_block) if b]
    return "\n".join(blocks) if blocks else "[Memory: UNAVAILABLE]"


async def mem_turn(
    mm: MemoryManager | None,
    intent: str,
    registry: "Registry",
) -> str:
    """Recall memory and store any new facts from intent concurrently; returns [Memory] block.

    The returned context excludes ``dag:*`` working-memory entries by
    default — DAG status / results belong in the dedicated DAG-update
    channel that ``GoatSupervisor.run`` surfaces via
    ``collect_finished``. To read the unfiltered view, call
    ``working_memory_block(mm, include_dag=True)`` directly. Each
    working-memory line carries a ``[FRESHNESS][SOURCE]`` prefix so
    GOAT can decide trust per entry.

    REGISTRY INJECTION (PHASE 4):
    =============================
    Requires registry parameter. Passed to maybe_store_info() for settings access.
    """
    ctx, _ = await asyncio.gather(
        recall_context(mm, intent),
        maybe_store_info(mm, intent, registry),
    )
    return ctx
