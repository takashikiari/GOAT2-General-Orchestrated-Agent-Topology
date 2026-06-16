"""Episodic sliding window — capacity management via pure-Python relevance scoring.

Episodic memory has NO TTL; this sliding window bounds it. When the entry count
reaches the limit, the oldest non-permanent entries are scored and acted on:

  recency_score  = 1.0 / (age_hours + 1)
  access_score   = min(1.0, access_count / 10)
  score          = recency_score * 0.6 + access_score * 0.4

  score < 0.3    → deleted (low value)
  score >= 0.7   → marked permanent (kept forever, skipped by future windows)
  0.3 – 0.7      → kept (eligible again next time)

permanent=True entries are never scored or deleted.
Backends are passed in — no singletons, no module state.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from memory.episodic.backend_protocol import EpisodicMemoryBackend

log = logging.getLogger("goat2.memory.episodic.sliding_window")

__all__ = ["check_and_slide"]

MAX_ENTRIES: int = 300
WARN_THRESHOLD: int = 280
_SLIDE_BATCH: int = 100
_FETCH_MAX: int = 400
_DELETE_BELOW: float = 0.3
_PERMANENT_ABOVE: float = 0.7


def _field(entry, name: str, default=None):
    """Read ``name`` from an entry that may be a dict or a MemoryEntry."""
    if isinstance(entry, dict):
        return entry.get(name, default)
    return getattr(entry, name, default)


def _meta(entry) -> dict:
    """Return the entry's metadata dict (empty dict if missing)."""
    m = _field(entry, "metadata", {}) or {}
    return m if isinstance(m, dict) else {}


def _score_entry(entry) -> float:
    """Pure-Python relevance score from recency + access frequency."""
    now = time.time()
    created_ts = float(_meta(entry).get("created_at_ts") or _field(entry, "created_at_ts", now) or now)
    age_hours = max(0.0, (now - created_ts) / 3600.0)
    recency_score = 1.0 / (age_hours + 1)

    access_count = int(_meta(entry).get("access_count", 0))
    access_score = min(1.0, access_count / 10.0)

    return recency_score * 0.6 + access_score * 0.4


async def _fallback_delete(backend: "EpisodicMemoryBackend", agent_role: str, candidates: list) -> None:
    """Delete the oldest ``_SLIDE_BATCH`` non-permanent entries (error fallback)."""
    removed = 0
    for entry in candidates[:_SLIDE_BATCH]:
        try:
            await backend.delete(agent_role, _field(entry, "key", ""))
            removed += 1
        except Exception as exc:
            log.debug("fallback delete failed: %s", exc)
    log.info("episodic(%s): fallback deleted oldest %d", agent_role, removed)


async def check_and_slide(
    backend: "EpisodicMemoryBackend", agent_role: str, max_entries: int = MAX_ENTRIES
) -> None:
    """Enforce the episodic capacity bound for ``agent_role`` via pure-Python scoring.

    WARNs as the count approaches ``max_entries``. At/above the limit, scores the
    oldest non-permanent entries and deletes/keeps/marks-permanent per the bands.
    Falls back to deleting the oldest ``_SLIDE_BATCH`` on any failure. Never raises.

    permanent=True entries are never scored or deleted.
    """
    try:
        count = await backend.count(agent_role)
        if count >= WARN_THRESHOLD:
            log.warning("episodic(%s): approaching limit (%d/%d)", agent_role, count, max_entries)
        if count < max_entries:
            return

        entries = await backend.list(agent_role, limit=_FETCH_MAX)
        candidates = [e for e in entries if not bool(_meta(e).get("permanent", False))]
        candidates.sort(key=lambda e: float(_meta(e).get("created_at_ts") or 0))
        if not candidates:
            log.warning("episodic(%s): over limit but all entries are permanent", agent_role)
            return

        batch = candidates[: max(_SLIDE_BATCH, count - max_entries + 1)]
        log.info("episodic(%s): at limit (%d/%d) — scoring %d oldest", agent_role, count, max_entries, len(batch))

        deleted = promoted = kept = 0
        for entry in batch:
            key = _field(entry, "key", "")
            content = _field(entry, "content", "") or ""
            try:
                score = _score_entry(entry)
                log.debug("episodic relevance %s: score=%.2f", key, score)
                if score < _DELETE_BELOW:
                    await backend.delete(agent_role, key)
                    deleted += 1
                elif score >= _PERMANENT_ABOVE:
                    meta = {"permanent": True, "compartment": _meta(entry).get("compartment", "")}
                    await backend.store(agent_role, key, content, metadata=meta)
                    promoted += 1
                else:
                    kept += 1
            except Exception as exc:
                log.debug("episodic slide op failed for %s: %s", key, exc)

        log.info("episodic(%s): deleted=%d permanent=%d kept=%d", agent_role, deleted, promoted, kept)
    except Exception as exc:
        log.debug("check_and_slide failed: %s", exc)
