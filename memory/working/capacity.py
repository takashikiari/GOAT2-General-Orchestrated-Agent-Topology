"""Working memory capacity management — configurable via ``config/memory.toml``.

When working memory is full, the oldest non-``dag:`` entries are candidates for
promotion to the episodic tier. Each candidate is scored by recency and access
frequency — no LLM call, no external dependencies.

  recency_score  = 1.0 / (age_hours + 1)
  access_score   = min(1.0, access_count / 10)
  score          = recency_score * 0.6 + access_score * 0.4

  score >= EPISODIC_PROMOTE_THRESHOLD   → promote to episodic + remove from working
  score <  EPISODIC_DROP_THRESHOLD      → drop (remove from working only)

``dag:`` entries are never promoted or scored.

CONFIG:
    Reads ``[working].max_entries`` and ``[working].warn_threshold`` from
    ``config/memory.toml`` at import time. Falls back to
    ``WORKING_MAX_ENTRIES`` / ``WORKING_WARN_THRESHOLD`` from
    ``config.fallbacks`` when the toml is absent. Promote/drop
    thresholds come from ``EPISODIC_PROMOTE_THRESHOLD`` /
    ``EPISODIC_DROP_THRESHOLD`` (scoring bands, not capacity).
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from config.fallbacks import (
    EPISODIC_DROP_THRESHOLD,
    EPISODIC_PROMOTE_THRESHOLD,
    WORKING_MAX_ENTRIES,
    WORKING_WARN_THRESHOLD,
)
from config.modular_loader import load_memory_config

if TYPE_CHECKING:
    from memory.working.backend_protocol import WorkingMemoryBackend

log = logging.getLogger("goat2.memory.working.capacity")

# Section-level defaults pulled from ``config/memory.toml`` at import
# time. When the toml is missing these stay at the fallback values.
_working = load_memory_config().get("working", {})
MAX_ENTRIES: int = int(_working.get("max_entries", WORKING_MAX_ENTRIES))
WARN_THRESHOLD: int = int(_working.get("warn_threshold", WORKING_WARN_THRESHOLD))
del _working

# Promote/drop bands are part of the scoring vocabulary, not the
# working-capacity cap, but they live here too for caller convenience.
PROMOTE_THRESHOLD: float = EPISODIC_PROMOTE_THRESHOLD
DROP_THRESHOLD: float = EPISODIC_DROP_THRESHOLD


async def get_promotable_entries(backend: "WorkingMemoryBackend", agent_role: str) -> list[dict]:
    """Return all non-dag entries for ``agent_role`` sorted oldest first."""
    try:
        keys = await backend.keys(agent_role)
        entries: list[dict] = []
        for key in keys:
            if "dag:" in str(key):
                continue
            record = await backend.get(agent_role, key)
            if record:
                entries.append(record)
        entries.sort(key=lambda e: e.get("created_at_ts", 0))
        log.debug("get_promotable_entries(%s): %d promotable (dag:* excluded)", agent_role, len(entries))
        return entries
    except Exception as exc:
        log.debug("get_promotable_entries failed: %s", exc)
        return []


def _score_entry(entry: dict) -> float:
    """Pure-Python relevance score from recency + access frequency."""
    now = time.time()
    created_ts = entry.get("created_at_ts", now)
    age_hours = max(0.0, (now - created_ts) / 3600.0)
    recency_score = 1.0 / (age_hours + 1)

    metadata = entry.get("metadata") or {}
    access_count = int(metadata.get("access_count", 0))
    access_score = min(1.0, access_count / 10.0)

    return recency_score * 0.6 + access_score * 0.4


async def check_and_promote(
    working_backend: "WorkingMemoryBackend",
    episodic_backend,
    agent_role: str,
    max_entries: int = MAX_ENTRIES,
) -> int:
    """Enforce capacity: score oldest entries, promote relevant ones, drop the rest.

    WARNs as the count approaches ``max_entries``. At the limit, the oldest
    promotable entries are scored: score >= 0.5 → promote to episodic; < 0.5 → drop.
    ``dag:`` entries are never touched.

    Returns:
        Number of entries successfully promoted to episodic.
    """
    try:
        count = len(await working_backend.keys(agent_role))
        if count >= WARN_THRESHOLD:
            log.warning("capacity(%s): approaching limit (%d/%d)", agent_role, count, max_entries)
        if max_entries > 0 and count < max_entries:
            log.debug("capacity(%s): under limit (%d/%d) — no promotion", agent_role, count, max_entries)
            return 0
        log.info("capacity(%s): at limit (%d/%d) — scoring oldest for promotion", agent_role, count, max_entries)

        promotable = await get_promotable_entries(working_backend, agent_role)
        to_process = promotable[: max(1, count - max_entries + 1)]

        promoted = dropped = 0
        for entry in to_process:
            key = entry.get("key", "")
            content = entry.get("content", "")
            score = _score_entry(entry)
            promote = score >= PROMOTE_THRESHOLD
            log.debug("capacity(%s): key=%s score=%.2f promote=%s", agent_role, key, score, promote)
            try:
                if promote and episodic_backend and content:
                    await episodic_backend.store(
                        agent_role, key, content, metadata=entry.get("metadata") or None,
                    )
                    promoted += 1
                else:
                    dropped += 1
                await working_backend.delete(agent_role, key)
            except Exception as exc:
                log.debug("promote/drop entry failed: %s", exc)

        remaining = max(0, count - promoted - dropped)
        log.info("capacity(%s): promoted %d, dropped %d (remaining ~%d entries)",
                 agent_role, promoted, dropped, remaining)
        return promoted
    except Exception as exc:
        log.debug("check_and_promote failed: %s", exc)
        return 0
