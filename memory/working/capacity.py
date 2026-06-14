"""Working-memory capacity management — bound size, auto-promote the oldest.

Working memory is a small, fast, session-scoped tier. Left unbounded it grows
until entries expire by TTL, which can bloat context and slow scans. This module
keeps each ``agent_role`` at or below a fixed number of entries by promoting the
oldest **turn** entries to the episodic tier before a new write lands.

Namespace isolation: keys in the ``dag:`` namespace are DAG coordination state
and are NEVER auto-promoted — they are excluded from promotion and expire via
their own TTL. Only conversational/turn entries (everything that is not ``dag:``)
are promotable.

All functions are pure utilities — no singletons, no module state. Backends are
passed in and must satisfy the working-memory backend Protocol; the episodic
backend only needs an async ``store(agent_role, key, content, metadata=...)``.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from memory.working.working_record import RecordDict

log = logging.getLogger("goat2.memory.working.capacity")

__all__ = ["get_promotable_entries", "promote_oldest", "check_and_promote"]

# Entries are considered "approaching capacity" within this many of the max.
_WARN_HEADROOM: int = 5

# Namespace prefix that is never auto-promoted (DAG coordination state).
_PROTECTED_PREFIX: str = "dag:"


async def get_promotable_entries(working_backend, agent_role: str) -> list["RecordDict"]:
    """Return promotable working records for ``agent_role``, oldest first.

    Excludes the protected ``dag:`` namespace. Records are sorted ascending by
    ``created_at_ts`` so the caller can promote the oldest entries first.

    Args:
        working_backend: Backend satisfying the working-memory Protocol.
        agent_role: Namespace whose entries are being considered.

    Returns:
        Promotable records (``dag:`` excluded) sorted oldest → newest.
    """
    keys = await working_backend.keys(agent_role)
    entries: list["RecordDict"] = []
    for k in keys:
        if str(k).startswith(_PROTECTED_PREFIX):
            continue
        record = await working_backend.get(agent_role, k)
        if record is not None:
            entries.append(record)
    entries.sort(key=lambda r: float(r.get("created_at_ts") or 0.0))
    log.debug(
        "get_promotable_entries(%s): %d promotable (dag:* excluded)",
        agent_role, len(entries),
    )
    return entries


async def promote_oldest(
    entries: list["RecordDict"], episodic_backend, agent_role: str, count: int
) -> int:
    """Promote the oldest ``count`` entries to the episodic tier.

    Writes each record's content + metadata to ``episodic_backend``. Does not
    delete from working memory — the caller owns the working backend and removes
    the promoted keys once promotion succeeds. Backend failures are logged at
    ERROR and skipped so one bad entry does not abort the batch.

    Args:
        entries: Promotable records, oldest first (from get_promotable_entries).
        episodic_backend: Episodic layer with async ``store(...)``.
        agent_role: Namespace being promoted.
        count: How many of the oldest entries to promote.

    Returns:
        Number of entries successfully written to the episodic tier.
    """
    promoted = 0
    for record in entries[:count]:
        key = record.get("key", "")
        content = record.get("content", "")
        try:
            await episodic_backend.store(
                agent_role, key, content, metadata=record.get("metadata") or None
            )
            promoted += 1
            log.debug("promote_oldest: %s → episodic", key)
        except Exception as exc:
            log.error("promote_oldest: failed to promote %s: %s", key, exc)
    return promoted


async def check_and_promote(
    working_backend, episodic_backend, agent_role: str, max_entries: int = 50
) -> int:
    """Enforce the working-memory capacity bound for ``agent_role``.

    Logs a WARNING as the count approaches ``max_entries`` (within
    ``_WARN_HEADROOM``). At or above the limit, promotes the oldest promotable
    entries to episodic and deletes them from working memory so a pending write
    keeps the tier at or below ``max_entries``. ``dag:`` entries are never
    promoted; if the tier is full of only ``dag:`` entries, nothing is promoted
    (logged at WARNING).

    Args:
        working_backend: Backend satisfying the working-memory Protocol.
        episodic_backend: Episodic layer with async ``store(...)``.
        agent_role: Namespace to enforce capacity on.
        max_entries: Maximum entries allowed (default 50).

    Returns:
        Number of entries promoted (and removed from working memory).
    """
    count = len(await working_backend.keys(agent_role))

    if count < max_entries:
        if count >= max_entries - _WARN_HEADROOM:
            log.warning("capacity(%s): approaching limit (%d/%d)", agent_role, count, max_entries)
        else:
            log.debug("capacity(%s): %d/%d entries", agent_role, count, max_entries)
        return 0

    log.info("capacity(%s): at limit (%d/%d) — promoting oldest", agent_role, count, max_entries)
    entries = await get_promotable_entries(working_backend, agent_role)
    to_promote = min(len(entries), count - max_entries + 1)
    if to_promote <= 0:
        log.warning("capacity(%s): at limit but no promotable entries (all dag:*)", agent_role)
        return 0

    promoted = await promote_oldest(entries, episodic_backend, agent_role, to_promote)
    for record in entries[:promoted]:
        try:
            await working_backend.delete(agent_role, record.get("key", ""))
        except Exception as exc:
            log.error("capacity(%s): delete-after-promote failed for %s: %s",
                      agent_role, record.get("key"), exc)
    log.info("capacity(%s): promoted %d → episodic (now ~%d entries)",
             agent_role, promoted, count - promoted)
    return promoted
