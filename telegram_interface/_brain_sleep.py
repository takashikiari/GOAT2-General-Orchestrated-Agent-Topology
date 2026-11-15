"""telegram_interface._brain_sleep — starts the memory brain's sleep loop with the bot (Laris, 2026-11-15).

Same convention as _coord_poller / _plugin_scanner: ``post_init_hook`` returns (post_init, post_shutdown). With
[brain] enabled = false it does nothing; a failure to start is logged and never blocks bot startup. The loop
(brain.brain.Brain.run_forever) reads the admin chat's L2 read-only every ``interval_seconds`` and shares the one
Brain instance (connection + lock) with the request hook in Orchestrator.run. Shutdown stops it between cycles
and waits for the cycle in progress (brain.stop_sleep), so no write is ever cut.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from brain import brain as _brain
from brain import scheduler as _scheduler
from utils.logging.setup import get_logger

if TYPE_CHECKING:
    from telegram.ext import Application

    from registry.registry import ServiceRegistry

log = get_logger(__name__)

__all__ = ["post_init_hook"]


def _scheduler_tick():
    """The scheduled messages on the brain's tick, sent through notify(), or None when [brain] scheduler is off."""
    if not _brain.CONFIG.get("scheduler"):
        return None
    from telegram_interface.notify import notify

    async def tick() -> None:
        await _scheduler.run_due(_brain.get_instance(), notify,
                                 max_attempts=int(_brain.CONFIG.get("scheduler_max_attempts", 3)))
    return tick


def post_init_hook(registry: "ServiceRegistry", chat_id: str):
    tasks: list[asyncio.Task] = []

    async def _post_init(application: "Application") -> None:
        if not _brain.is_enabled():
            log.info("brain: disabled via config, sleep not started")
            return
        if not chat_id:
            log.warning("brain: no admin chat configured, sleep not started")
            return
        try:
            tick = _scheduler_tick()
            if tick is not None:  # a message left half-sent by the previous process goes out again
                recovered = await _scheduler.recover(_brain.get_instance())
                log.info("brain: scheduler on, recovered=%d", recovered)
            task = _brain.start(registry.working_memory, [chat_id], on_tick=tick)
        except Exception as exc:  # noqa: BLE001 - the brain must never block the bot
            log.warning("brain: sleep failed to start: %s: %s", type(exc).__name__, exc)
            return
        if task is not None:
            tasks.append(task)
            log.info("brain: sleep started chat=%s interval=%ss db=%s", chat_id, _brain.CONFIG["interval_seconds"],
                     _brain.CONFIG["db_path"])

    async def _post_shutdown(application: "Application") -> None:
        if tasks:  # stop between cycles and wait for the one in progress: never cut a write
            await _brain.stop_sleep()
            log.info("brain: sleep stopped")

    return _post_init, _post_shutdown
