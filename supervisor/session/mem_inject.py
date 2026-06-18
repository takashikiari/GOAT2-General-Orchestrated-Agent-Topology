"""Fan-out recall across all three memory tiers and concurrent info extraction per turn.

FRESHNESS + SOURCE SCORING (configured via ``config/memory.toml [freshness]``):
  freshness (age at read time):
    < fresh_max_seconds   → [FRESH]
    < recent_max_seconds  → [RECENT]
    else                  → [OLD]
  source (key prefix):
    turn:*  → [CONV]   dag:*  → [DAG]
    goat:*  → [GOAT]   other  → [SYS]

Lines: ``- [FRESH][CONV] turn:abc: hello``

NAMESPACE FILTER (``should_include_entry``):
  CONV / GOAT / SYS → always admitted.
  DAG → admitted iff age < dag_max_age_seconds OR intent mentions
        one of: dag, task, result, workflow, pipeline.

Sort order: CONV+FRESH first, DAG+OLD last; within a bucket, newest first.
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

__all__ = ["mem_turn", "recall_context", "working_memory_block", "should_include_entry"]

_LIMIT: Final[int] = 5
_WM_LIMIT: Final[int] = 50
log = logging.getLogger("goat2.supervisor.session")

_DAG_PREFIX:  Final[str] = "dag:"
_TURN_PREFIX: Final[str] = "turn:"
_GOAT_PREFIX: Final[str] = "goat:"
_DAG_INTENT_KEYWORDS: Final[tuple[str, ...]] = (
    "dag", "task", "result", "workflow", "pipeline",
)
_FRESHNESS_DEFAULTS: Final[dict[str, float]] = {
    "fresh_max_seconds":   300.0,   # 5 min
    "recent_max_seconds":  3600.0,  # 60 min
    "dag_max_age_seconds": 600.0,   # 10 min
}


def _load_freshness_config() -> dict[str, float]:
    """Read [freshness] from config/memory.toml; fall back to defaults on any failure."""
    cfg: dict[str, float] = dict(_FRESHNESS_DEFAULTS)
    try:
        from config.modular_loader import load_memory_config
        section = (load_memory_config() or {}).get("freshness", {}) or {}
        for key in _FRESHNESS_DEFAULTS:
            if key in section and section[key] is not None:
                try:
                    cfg[key] = float(section[key])
                except (TypeError, ValueError):
                    log.debug("mem_inject: freshness.%s=%r not numeric — using default",
                              key, section[key])
    except Exception as exc:  # noqa: BLE001 — never block on config
        log.debug("mem_inject: [freshness] load skipped: %s", exc)
    return cfg


# Loaded once at import — pure read of a static toml.
_FRESHNESS_CFG: Final[dict[str, float]] = _load_freshness_config()


def _source_label(key: object) -> str:
    if not isinstance(key, str):
        return "[SYS]"
    if key.startswith(_DAG_PREFIX):
        return "[DAG]"
    if key.startswith(_TURN_PREFIX):
        return "[CONV]"
    if key.startswith(_GOAT_PREFIX):
        return "[GOAT]"
    return "[SYS]"


def _freshness_label(record: dict, now: float) -> str:
    """Bucket age; unparseable timestamps → [OLD] (safest)."""
    try:
        age = now - float(record.get("created_at_ts"))
    except (TypeError, ValueError):
        return "[OLD]"
    if age < _FRESHNESS_CFG["fresh_max_seconds"]:
        return "[FRESH]"
    if age < _FRESHNESS_CFG["recent_max_seconds"]:
        return "[RECENT]"
    return "[OLD]"


def _format_line(record: dict, now: float) -> str:
    """``- [FRESH][CONV] key: preview``."""
    key = record.get("key", "?")
    content = (record.get("content") or "").strip().replace(chr(10), " ")
    return f"- {_freshness_label(record, now)}{_source_label(key)} {key}: {content}"


def _sort_key(record: dict, now: float) -> tuple[int, int, float]:
    """Trust-high first: (source_rank, freshness_rank, age)."""
    src_rank = {"[CONV]": 0, "[GOAT]": 1, "[SYS]": 2, "[DAG]": 3}[_source_label(record.get("key"))]
    fr_rank = {"[FRESH]": 0, "[RECENT]": 1, "[OLD]": 2}[_freshness_label(record, now)]
    try:
        age = max(0.0, now - float(record.get("created_at_ts") or 0.0))
    except (TypeError, ValueError):
        age = float("inf")
    return (src_rank, fr_rank, age)


def should_include_entry(entry: dict, intent: str, now: float) -> bool:
    """Admit a working-memory entry into GOAT's context block. Pure logic, no LLM.

    CONV/GOAT/SYS → always admitted. DAG → admitted iff recent
    (< dag_max_age_seconds) or intent mentions a DAG keyword.
    """
    key = entry.get("key", "")
    if not isinstance(key, str):
        return True
    if key.startswith((_TURN_PREFIX, _GOAT_PREFIX)):
        return True
    if key.startswith(_DAG_PREFIX):
        try:
            age = now - float(entry.get("created_at_ts"))
        except (TypeError, ValueError):
            age = float("inf")
        if age < _FRESHNESS_CFG["dag_max_age_seconds"]:
            return True
        return any(kw in (intent or "").lower() for kw in _DAG_INTENT_KEYWORDS)
    return True


async def working_memory_block(mm: MemoryManager | None, *, include_dag: bool = False) -> str:
    """Build a '[Working Memory]' block; each line is ``- [FRESH][CONV] key: preview``.

    Loads every entry (up to ``_WM_LIMIT``) directly from the working
    backend. Each line is sorted trust-high first
    (CONV+FRESH first, DAG+OLD last). DAG entries pass through
    ``should_include_entry`` with an empty intent by default; pass
    ``include_dag=True`` to bypass the filter for callers that need
    the unfiltered view. Returns '' on failure or when empty.
    """
    if mm is None:
        return ""
    try:
        backend = mm.working.backend
        now = time.time()
        records: list[dict] = []
        dropped_dag = 0
        for k in await backend.keys(SESSION_ROLE):
            rec = await backend.get(SESSION_ROLE, k)
            if not rec:
                continue
            if _source_label(k) == "[DAG]" and not include_dag:
                if not should_include_entry(rec, "", now):
                    dropped_dag += 1
                    continue
            records.append(rec)
        if not records:
            return ""
        records.sort(key=lambda r: _sort_key(r, now))
        if dropped_dag:
            log.debug("working_memory_block: filtered %d dag:* keys", dropped_dag)
        lines = [_format_line(r, now) for r in records[:_WM_LIMIT]]
        log.debug("working_memory_block: %d entries (filtered %d dag)", len(lines), dropped_dag)
        return "[Working Memory]\n" + "\n".join(lines)
    except Exception as exc:
        log.debug("working_memory_block failed: %s", exc)
        return ""


async def recall_context(mm: MemoryManager | None, query: str) -> str:
    """Return cross-tier '[Memory]' fan-out PLUS conversational '[Working Memory]' block."""
    if mm is None:
        return "[Memory: UNAVAILABLE]"
    try:
        hits = await mm.recall(SESSION_ROLE, query, limit=_LIMIT)
        lines = [h.content.strip() for h in hits if h.content.strip()]
        mem_block = ("[Memory]\n" + "\n".join(f"- {ln}" for ln in lines)) if lines else ""
    except Exception as exc:
        log.error("recall_context fan-out failed: %s: %s", type(exc).__name__, exc)
        mem_block = ""
    wm_block = await working_memory_block(mm)
    blocks = [b for b in (mem_block, wm_block) if b]
    return "\n".join(blocks) if blocks else "[Memory: UNAVAILABLE]"


async def mem_turn(mm: MemoryManager | None, intent: str, registry: "Registry") -> str:
    """Recall memory and store any new facts from intent concurrently; returns [Memory] block.

    REGISTRY INJECTION (PHASE 4):
    =============================
    Requires registry parameter. Passed to maybe_store_info() for settings access.
    """
    ctx, _ = await asyncio.gather(
        recall_context(mm, intent),
        maybe_store_info(mm, intent, registry),
    )
    return ctx
