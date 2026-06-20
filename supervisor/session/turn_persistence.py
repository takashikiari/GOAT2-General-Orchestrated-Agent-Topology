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
        # NOTE: schedule_promotion is itself responsible for
        # creating its own asyncio task and registering it in
        # supervisor._background_tasks (BUG-027). Do NOT wrap
        # it in asyncio.create_task here — that would pass
        # schedule_promotion's None return value to create_task,
        # which raises "a coroutine was expected, got None".
        schedule_promotion(supervisor, turn_count)
    except Exception as exc:  # noqa: BLE001 — never break the turn
        log.warning("store_and_promote failed: %s", exc)


async def _store_turn(
    mm,
    turn_count: int,
    intent: str,
    summary: str,
) -> None:
    """Store one turn as two separate working-memory records.

    The intent and the assistant summary are written under distinct keys
    so the style analyzer (``_learn_and_persist``) can train on user
    input alone. Bundling them into a single payload would mix prior
    GOAT responses with user input, biasing the learned style profile
    toward the assistant's voice instead of the user's.

    Args:
        mm: MemoryManager.
        turn_count: 1-based turn number.
        intent: Raw user intent for this turn.
        summary: Assistant's user-facing summary for this turn.

    Returns:
        None. Best-effort; never raises.
    """
    try:
        await mm.store(SESSION_ROLE, f"turn:{turn_count}:intent", intent or "")
        await mm.store(SESSION_ROLE, f"turn:{turn_count}:summary", summary or "")
    except Exception as exc:  # noqa: BLE001
        log.debug("_store_turn failed: %s", exc)


# How many recent user-intent entries the analyzer reads.
# Doubled vs the legacy window so the ``e.key.endswith(":intent")`` filter
# still yields ≥ ``min_turns_to_learn`` samples after dropping summaries.
_INTENT_WINDOW: Final[int] = _ANALYZER_WINDOW * 2


async def _learn_and_persist(supervisor: "GoatSupervisor", mm) -> bool:
    """Run the analyzer, write to Letta, return True on successful write.

    Steps:
        1. Load the existing persona block from Letta (the merged baseline
           against which the new style will be diffed). Without this read,
           every turn overwrites the profile with a standalone one and
           incremental learning is lost.
        2. Read recent user-intent entries (key suffix ``:intent`` only —
           never the assistant summaries, which would bias the profile).
        3. Call ``analyze_style(user_turns, existing)`` so the new profile
           merges over the old.
        4. Write the merged profile back to Letta.

    Args:
        supervisor: The live GoatSupervisor (for ``mm`` access).
        mm: The registry's MemoryManager.

    Returns:
        True when a new profile was written, False on any failure or when
        the analyzer returns empty text.
    """
    try:
        from supervisor.behavior.style_learner import analyze_style
        from supervisor.behavior.store import load_style, save_style
        existing = await load_style(mm) or ""
        entries = await mm.working.list(SESSION_ROLE, limit=_INTENT_WINDOW)
        user_turns = [
            e.content for e in entries
            if e and e.content and getattr(e, "key", "").endswith(":intent")
        ]
        if not user_turns:
            return False
        new_text = await analyze_style(user_turns, existing)
        if not new_text:
            return False
        return bool(await save_style(mm, new_text))
    except Exception as exc:  # noqa: BLE001
        log.debug("_learn_and_persist failed: %s", exc)
        return False


async def _do_promote(supervisor: "GoatSupervisor", turn_count: int) -> None:
    """Run the actual ``mm.promote_turns`` call. Body of the
    background task — split out so the wrapper can wrap it in
    error logging + task registration."""
    mm = getattr(supervisor, "memory_manager", None)
    if mm is None:
        return
    await mm.promote_turns(SESSION_ROLE, turn_count)


def schedule_promotion(supervisor: "GoatSupervisor", turn_count: int) -> None:
    """Promote conversation turns through memory tiers (background).

    BUG-027 fix: the task is now registered on
    ``supervisor._background_tasks`` (key ``turn-promotion:<n>``)
    so it can be awaited at session end. Exceptions inside the
    task are logged at WARNING (not DEBUG) so recurring failures
    are visible. The function itself is sync (fire-and-forget);
    awaiting the actual work is the task's job.
    """
    mm = getattr(supervisor, "memory_manager", None)
    if mm is None:
        return
    registry = getattr(supervisor, "_background_tasks", None)
    if registry is None:
        # No registry available — fall back to the legacy
        # detached behaviour. The task is fire-and-forget and
        # exceptions are silently lost. This path is only used
        # by tests that build a bare supervisor stub.
        try:
            asyncio.create_task(_do_promote(supervisor, turn_count))
        except RuntimeError:
            # No event loop — give up silently.
            pass
        return
    key = f"turn-promotion:{turn_count}"

    async def _runner() -> None:
        try:
            await _do_promote(supervisor, turn_count)
        except Exception as exc:  # noqa: BLE001
            log.warning("schedule_promotion failed (turn=%d): %s", turn_count, exc)
        finally:
            # Always remove the task from the registry on exit so
            # finalize_background_tasks can drain cleanly.
            registry.pop(key, None)

    try:
        task = asyncio.create_task(_runner(), name=key)
    except RuntimeError:
        # No event loop running — cannot schedule.
        log.debug("schedule_promotion: no running event loop — skipping turn=%d", turn_count)
        return
    registry[key] = task
