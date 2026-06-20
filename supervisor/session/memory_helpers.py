"""Memory fetch helpers — working / episodic / persona retrievers.

Extracted from ``supervisor/session/mem_inject.py`` to keep that
file under the 260-line ceiling. This module owns the
low-level data fetching:

  - ``_list_working``  — pull recent working-memory records
  - ``_filter_dag``    — drop dag:* entries by default
  - ``_ts_of``         — tolerant timestamp accessor
  - ``_bucket_by_age`` — split records into temporal buckets
  - ``_fetch_episodic_hits`` — ChromaDB recall with timeout
  - ``_fetch_persona``  — Letta persona block

All functions are pure orchestration — they accept a
MemoryManager and return data, with no global state and
no LLM calls.

USAGE:
    from supervisor.session.memory_helpers import (
        list_working, filter_dag, ts_of, bucket_by_age,
        fetch_episodic_hits, fetch_persona,
    )
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from memory.shared import MemoryManager

from config.roles import SESSION_ROLE
from supervisor.mechanisms.namespace import is_dag_key

log = logging.getLogger("goat2.supervisor.session.memory_helpers")

__all__ = [
    "list_working",
    "filter_dag",
    "ts_of",
    "bucket_by_age",
    "fetch_episodic_hits",
]

# Cap on the working-memory round-trip; the legacy default
# from the pre-Faza-2 era, kept as a defensive ceiling.
_WM_LIMIT: Final[int] = 50

# Default hard cap on the episodic recall call. The caller
# can override via the top_k parameter.
_EPISODIC_DEFAULT_TOP_K: Final[int] = 5

# Default timeout for the ChromaDB round-trip.
_EPISODIC_TIMEOUT_S: Final[float] = 3.0


# ── Working memory fetchers ───────────────────────────────────────────────


async def list_working(mm: "MemoryManager") -> list:
    """Fetch recent working-memory records for the SESSION_ROLE.

    Defensive: any backend failure returns ``[]`` rather than
    raising — a memory hiccup must not break the turn.
    """
    try:
        backend = mm.working.backend
        keys = await backend.keys(SESSION_ROLE)
        records: list = []
        for k in keys[:_WM_LIMIT]:
            rec = await backend.get(SESSION_ROLE, k)
            if rec:
                records.append(rec)
        return records
    except Exception as exc:  # noqa: BLE001
        log.debug("list_working failed: %s", exc)
        return []


async def filter_dag(records: list, include_dag: bool) -> list:
    """Drop ``dag:*`` entries unless ``include_dag`` is set.

    Accepts both dict-shaped records and SimpleNamespace /
    MemoryEntry-shaped records (the latter expose attributes,
    not ``.get()``).
    """
    if include_dag:
        return list(records)

    def _key_of(r) -> str:
        if isinstance(r, dict):
            return r.get("key", "")
        return str(getattr(r, "key", "") or "")

    return [r for r in records if not is_dag_key(_key_of(r))]


def ts_of(record) -> float:
    """Return the created_at_ts of a record, or 0.0 if missing.

    Accepts dict, SimpleNamespace, and MemoryEntry shapes.
    Looks for ``created_at_ts`` at the top level first, then
    inside ``metadata`` (where MemoryEntry keeps it).
    """
    if record is None:
        return 0.0
    if isinstance(record, dict):
        ts = record.get("created_at_ts")
    else:
        ts = getattr(record, "created_at_ts", None)
    if ts is not None:
        try:
            return float(ts)
        except (TypeError, ValueError):
            return 0.0
    meta = record.get("metadata") if isinstance(record, dict) else getattr(record, "metadata", None)
    if isinstance(meta, dict):
        try:
            return float(meta.get("created_at_ts") or 0.0)
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def bucket_by_age(
    records: list,
    *,
    now: float,
    present_max_age_s: float,
    present_past_max_age_s: float,
) -> tuple[list, list, list]:
    """Split records into (present, present_past, past) by age.

    Returns three lists. Records with missing timestamps
    (ts <= 0) go to ``past`` — they're old by assumption.
    """
    present: list = []
    present_past: list = []
    past: list = []
    for r in records:
        ts = ts_of(r)
        if ts <= 0.0:
            past.append(r)
            continue
        age = now - ts
        if age < present_max_age_s:
            present.append(r)
        elif age < present_past_max_age_s:
            present_past.append(r)
        else:
            past.append(r)
    return present, present_past, past


# ── Episodic + persona fetchers ───────────────────────────────────────────


async def fetch_episodic_hits(
    mm: "MemoryManager",
    query: str,
    top_k: int = _EPISODIC_DEFAULT_TOP_K,
    *,
    timeout_s: float = _EPISODIC_TIMEOUT_S,
) -> list:
    """Fetch episodic recall hits with a hard timeout.

    On any failure (timeout, exception, missing method), returns
    ``[]`` — the [Present-Past] layer renders without episodic
    hits but the rest of the structure is preserved.
    """
    try:
        hits = await asyncio.wait_for(
            mm.recall(SESSION_ROLE, query, limit=top_k),
            timeout=timeout_s,
        )
        return list(hits or [])
    except asyncio.TimeoutError:
        log.warning(
            "fetch_episodic_hits: timed out after %.1fs", timeout_s,
        )
        return []
    except Exception as exc:  # noqa: BLE001
        log.debug("fetch_episodic_hits failed: %s", exc)
        return []


async def fetch_persona(mm: "MemoryManager") -> str:
    """Fetch the Letta persona block. Returns empty string on
    failure (caller renders the unavailable marker)."""
    try:
        long_term = getattr(mm, "long_term", None)
        if long_term is None:
            return ""
        text = await long_term.get_block("goat", "persona")
        return text or ""
    except Exception as exc:  # noqa: BLE001
        log.debug("fetch_persona failed: %s", exc)
        return ""