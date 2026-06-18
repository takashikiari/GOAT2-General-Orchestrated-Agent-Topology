"""Turn persistence — store a completed turn to working memory,
trigger style analysis, schedule tier promotion, refresh the
in-memory style cache. Free functions over the live supervisor
instance (no singletons, no module-level state).

USAGE (from the supervisor):
    from supervisor.session.turn_persistence import store_and_promote

    await store_and_promote(supervisor, turn_count, intent, summary)

WHAT IT DOES:
  1. Stores the turn in working memory as a structured record
     (``turn:<n>`` key).
  2. Analyzes recent user turns and persists an updated style
     profile to Letta's ``persona`` block.
  3. If the style was actually written, refreshes the
     supervisor's in-memory ``_behavior_style`` so the next
     turn's system prompt sees the freshest style.
  4. Schedules the background tier-promotion task.

All steps degrade quietly on error so a memory hiccup never
breaks the turn.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Final

from config.roles import SESSION_ROLE

if TYPE_CHECKING:
    from supervisor.supervisor import GoatSupervisor

log = logging.getLogger("goat2.supervisor.session.turn_persistence")

__all__ = ["store_and_promote", "schedule_promotion"]

# How many recent working-memory entries the analyzer reads.
# Small window keeps the analyzer fast (O(n) scoring on a
# bounded list) and avoids stale turn influence.
_ANALYZER_WINDOW: Final[int] = 10


async def store_and_promote(
    supervisor: "GoatSupervisor",
    turn_count: int,
    intent: str,
    summary: str,
) -> None:
    """Persist the turn, learn style, refresh cache, schedule promotion.

    Args:
        supervisor: The live GoatSupervisor (source of mm, registry).
        turn_count: 1-based turn number (``len(history.messages)``).
        intent: The raw user intent for this turn.
        summary: The assistant's user-facing summary for this turn.

    Returns:
        None. Best-effort; never raises.
    """
    mm = getattr(supervisor, "memory_manager", None)
    if mm is None:
        return
    try:
        # 1. Persist this exchange to working memory.
        await _store_turn(mm, turn_count, intent, summary)
        log.debug("store_and_promote: turn %d persisted", turn_count)

        # 2. Behavioral learning — analyze + write + cache refresh.
        style_was_written = await _learn_and_persist(supervisor, mm)
        if style_was_written:
            # 3. Refresh the in-memory style cache so the next
            #    turn's system prompt sees the freshest profile.
            from supervisor.mechanisms.style_sync import refresh_style
            await refresh_style(supervisor)

        # 4. Schedule the background tier promotion.
        asyncio.create_task(
            schedule_promotion(supervisor, turn_count),
            name="turn-promotion",
        )
    except Exception as exc:  # noqa: BLE001 — never break the turn
        log.warning("store_and_promote failed: %s", exc)


async def _store_turn(
    mm,
    turn_count: int,
    intent: str,
    summary: str,
) -> None:
    """Store one turn as a structured working-memory record."""
    try:
        payload = (
            f"turn={turn_count}\n"
            f"intent={intent}\n"
            f"summary={summary}"
        )
        await mm.store(SESSION_ROLE, f"turn:{turn_count}", payload)
    except Exception as exc:  # noqa: BLE001
        log.debug("_store_turn failed: %s", exc)


async def _learn_and_persist(supervisor: "GoatSupervisor", mm) -> bool:
    """Run the analyzer, write to Letta, return True on successful write."""
    try:
        from supervisor.behavior.analyzer import analyze_style
        from supervisor.behavior.store import save_style
        entries = await mm.working.list(SESSION_ROLE, limit=_ANALYZER_WINDOW)
        user_turns = [e.content for e in entries if e and e.content]
        if not user_turns:
            return False
        new_text = await analyze_style(user_turns, supervisor.registry)
        if not new_text:
            return False
        return bool(await save_style(mm, new_text))
    except Exception as exc:  # noqa: BLE001
        log.debug("_learn_and_persist failed: %s", exc)
        return False


async def schedule_promotion(supervisor: "GoatSupervisor", turn_count: int) -> None:
    """Promote conversation turns through memory tiers (background task).

    Detached: errors are logged and swallowed — promotion is a
    background hygiene task and must never affect the turn path.
    """
    mm = getattr(supervisor, "memory_manager", None)
    if mm is None:
        return
    try:
        await mm.promote_turns(SESSION_ROLE, turn_count)
    except Exception as exc:  # noqa: BLE001
        log.debug("schedule_promotion failed (non-critical): %s", exc)
