"""Turn persistence & memory-tier promotion for GoatSupervisor.

Free functions that store a completed conversation turn into the working and
episodic tiers, trigger behavioral-style learning, and schedule promotion of
turns through the memory tiers as a background task. Extracted from
GoatSupervisor so the supervisor class stays focused on orchestration.

Each function takes the live ``supervisor`` instance for state access (history,
session id, registry, memory_manager) — no singletons, no module-level state.
All steps degrade quietly on error so a memory hiccup never breaks the turn.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from config.roles import SESSION_ROLE

if TYPE_CHECKING:
    from supervisor.supervisor import GoatSupervisor

log = logging.getLogger("goat2.supervisor.session.turn_persistence")

__all__ = ["store_and_promote", "schedule_promotion"]


async def store_and_promote(
    supervisor: "GoatSupervisor", turn_count: int, intent: str, summary: str
) -> None:
    """Store turn in working memory, auto-save to episodic tier, schedule promotion."""
    mm = supervisor.memory_manager
    if not mm:
        return
    try:
        from supervisor.session import store_turn, store_goat_turn
        await store_turn(mm, turn_count, intent, summary)
        await store_goat_turn(mm, supervisor._session_id, intent, summary)
        try:
            from memory.shared.hooks import auto_save_memory
            await auto_save_memory(mm, "user_session", intent, summary)
        except Exception as e:
            log.warning("auto_save_memory failed: %s", e)
        # Behavioral learning: analyze recent turns and persist the updated style.
        try:
            from supervisor.behavior.behavior_analyzer import analyze_style
            from supervisor.behavior.behavior_store import save_style
            from supervisor.behavior.behavior_session import get_recent_turns
            from supervisor.behavior.behavior_profile import serialize
            turns = await get_recent_turns(mm, limit=10)
            if turns:
                profile = await analyze_style(turns, supervisor.registry)
                if profile:
                    await save_style(mm, serialize(profile))
        except Exception as e:
            log.debug("behavior analysis skipped: %s", e)
        asyncio.create_task(schedule_promotion(supervisor, turn_count))
    except Exception as e:
        log.warning("Memory storage skipped: %s", e)


async def schedule_promotion(supervisor: "GoatSupervisor", turn_count: int) -> None:
    """Promote conversation turns through memory tiers (background task)."""
    mm = supervisor.memory_manager
    if not mm:
        return
    try:
        await mm.promote_turns(SESSION_ROLE, turn_count)
    except Exception as e:
        log.warning("Promotion task failed (non-critical): %s", e)
