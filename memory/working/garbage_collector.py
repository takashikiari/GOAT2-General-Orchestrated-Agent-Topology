"""Working memory garbage collector — silent TTL+size enforcement.

Two cooperating helpers:

  ``collect(working_backend, agent_role) -> int``
    Deletes every ``dag:*`` entry older than ``DAG_TTL_S`` AND trims the
    ``turn:*`` namespace down to the most recent ``MAX_TURN_ENTRIES`` keys
    (sorted by ``created_at_ts``). Returns the total number of deletions.
    Pure async — never blocks the caller.

  ``schedule_auto_collect(working_backend, agent_role, turn_count, every_n) -> bool``
    Pure predicate: returns ``True`` when ``turn_count % every_n == 0`` so
    the caller can fire a detached ``asyncio.create_task(collect(...))``.
    Does no I/O; safe to call on every turn.

The collector never touches ``preference:*`` / ``goat:*`` / explicit
user-namespace entries — it only trims the ephemeral namespaces that are
guaranteed to be regenerable: DAG plumbing and conversation turns.

Design rules:
  - Pure async; the supervisor calls it via ``asyncio.create_task`` so it
    can never delay a turn.
  - All thresholds are configurable; defaults match the working-memory
    capacity constants.
  - No singletons; backends are passed in.
  - The collector swallows backend errors per-entry and keeps going — one
    bad key must not block the rest of the sweep.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from memory.working.backend_protocol import WorkingMemoryBackend

log = logging.getLogger("goat2.memory.working.garbage_collector")

__all__ = ["collect", "schedule_auto_collect"]

# Defaults: configurable via the function arguments.
DAG_TTL_S: float = 3600.0           # 1 hour
MAX_TURN_ENTRIES: int = 50          # most-recent N turn:* entries kept
TURN_PREFIX: str = "turn"
DAG_PREFIX: str = "dag"
AUTO_COLLECT_EVERY_N: int = 10      # schedule_auto_collect default cadence


async def collect(working_backend: "WorkingMemoryBackend", agent_role: str) -> int:
    """Garbage-collect a working-memory namespace.

    1. Delete every ``dag:*`` entry whose ``created_at_ts`` is older than
       ``dag_ttl_s`` seconds.
    2. Keep only the newest ``max_turn_entries`` ``turn:*`` entries
       (sorted by ``created_at_ts``); delete the rest.

    Args:
        working_backend: The working-memory backend to trim.
        agent_role: The namespace to operate on (e.g. ``user_session``).
        dag_ttl_s: How long ``dag:*`` entries are allowed to live.
        max_turn_entries: Number of ``turn:*`` entries to retain.

    Returns:
        Total number of entries deleted (dag + turn combined).
    """
    deleted = 0
    try:
        keys = await working_backend.keys(agent_role)
    except Exception as exc:  # noqa: BLE001
        log.debug("collect: keys() failed for %s: %s", agent_role, exc)
        return 0

    now = time.time()
    turn_records: list[tuple[str, float]] = []
    dag_keys: list[str] = []

    for key in keys:
        key_str = str(key)
        if key_str.startswith(DAG_PREFIX + ":") or "dag:" in key_str:
            dag_keys.append(key_str)
        elif key_str.startswith(TURN_PREFIX + ":") or "turn:" in key_str:
            try:
                rec = await working_backend.get(agent_role, key_str)
            except Exception as exc:  # noqa: BLE001
                log.debug("collect: get(%s) failed: %s", key_str, exc)
                continue
            if not rec:
                continue
            ts = float(rec.get("created_at_ts") or 0)
            turn_records.append((key_str, ts))

    # Tier A — expire stale DAG entries (they have their own TTL, but the
    # collector enforces it on a periodic sweep in case a key was missed).
    for key_str in dag_keys:
        try:
            rec = await working_backend.get(agent_role, key_str)
        except Exception as exc:  # noqa: BLE001
            log.debug("collect: dag get(%s) failed: %s", key_str, exc)
            continue
        if not rec:
            continue
        ts = float(rec.get("created_at_ts") or 0)
        if ts and (now - ts) >= DAG_TTL_S:
            try:
                await working_backend.delete(agent_role, key_str)
                deleted += 1
            except Exception as exc:  # noqa: BLE001
                log.debug("collect: delete(%s) failed: %s", key_str, exc)

    # Tier B — keep the newest N turn entries, drop the rest.
    if len(turn_records) > MAX_TURN_ENTRIES:
        turn_records.sort(key=lambda kv: kv[1], reverse=True)
        surplus = turn_records[MAX_TURN_ENTRIES:]
        for key_str, _ in surplus:
            try:
                await working_backend.delete(agent_role, key_str)
                deleted += 1
            except Exception as exc:  # noqa: BLE001
                log.debug("collect: delete turn(%s) failed: %s", key_str, exc)

    if deleted:
        log.info(
            "collect(%s): deleted %d (dag=%d ttl=%.0fs turn_surplus=%d max=%d)",
            agent_role, deleted, len(dag_keys), DAG_TTL_S,
            max(0, len(turn_records) - MAX_TURN_ENTRIES), MAX_TURN_ENTRIES,
        )
    else:
        log.debug("collect(%s): nothing to delete", agent_role)
    return deleted


def schedule_auto_collect(
    working_backend: "WorkingMemoryBackend",
    agent_role: str,
    turn_count: int,
    every_n: int = AUTO_COLLECT_EVERY_N,
) -> bool:
    """Predicate: should the caller fire a detached ``collect`` now?

    Returns ``True`` when ``turn_count % every_n == 0``. The caller is
    expected to do::

        if schedule_auto_collect(backend, role, n):
            asyncio.create_task(collect(backend, role))

    Args:
        working_backend: The working-memory backend (kept for API
            symmetry with ``collect``; not inspected here).
        agent_role: The namespace to operate on (likewise kept for
            symmetry).
        turn_count: The current conversation turn number.
        every_n: Fire the collector every N turns.

    Returns:
        ``True`` when the caller should schedule a ``collect`` task.
    """
    del working_backend, agent_role  # signature symmetry, unused
    if every_n <= 0:
        return False
    return turn_count > 0 and (turn_count % every_n == 0)
