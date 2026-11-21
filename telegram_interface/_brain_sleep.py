"""telegram_interface._brain_sleep — starts the memory brain's sleep loop with the bot (Laris, 2026-11-15).

Same convention as _coord_poller / _plugin_scanner: ``post_init_hook`` returns (post_init, post_shutdown). With
[brain] enabled = false it does nothing; a failure to start is logged and never blocks bot startup. The loop
(brain.brain.Brain.run_forever) reads the admin chat's L2 read-only every ``interval_seconds`` and shares the one
Brain instance (connection + lock) with the request hook in Orchestrator.run. Shutdown stops it between cycles
and waits for the cycle in progress (brain.stop_sleep), so no write is ever cut.
"""
from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from brain import brain as _brain
from brain import scheduler as _scheduler
from utils.logging.setup import get_logger

if TYPE_CHECKING:
    from telegram.ext import Application

    from registry.registry import ServiceRegistry

log = get_logger(__name__)

__all__ = ["post_init_hook"]


def _scheduler_tick(layers=None):
    """The scheduled messages on the brain's tick, or None when [brain] scheduler is off. Each one is sent through
    notify() and then appended to the chat's L2 history as GOAT's own message (source "scheduled"), so the next turn
    sees that it went out (Laris, 2026-11-15: the 12:20 reminder must be in the conversation when he answers it).
    The L2 write comes after the send and only logs on failure: the message is out, it must never go out twice."""
    if not _brain.CONFIG.get("scheduler"):
        return None
    from telegram_interface.notify import notify

    async def send(chat_id: str, text: str) -> None:
        await notify(chat_id, text)
        if layers is None:
            return
        try:
            await layers.append_and_save_working_context(
                str(chat_id), {"role": "assistant", "content": text, "timestamp": time.time(), "source": "scheduled"})
        except Exception as exc:  # noqa: BLE001 - sent already: log, never resend
            log.warning("brain: scheduled message sent but not saved to history chat=%s: %s: %s", chat_id,
                        type(exc).__name__, exc)

    async def tick() -> None:
        await _scheduler.run_due(_brain.get_instance(), send,
                                 max_attempts=int(_brain.CONFIG.get("scheduler_max_attempts", 3)))
    return tick


def _graph_tick(registry):
    """The memory graph fed from L3 on the brain's tick (brain/graph_ingest.py), or None when [brain] graph is off.
    Phase F1: it only builds the graph; nothing reads it yet."""
    if not _brain.CONFIG.get("graph"):
        return None
    from brain import graph_ingest

    async def tick() -> None:
        cfg = _brain.CONFIG
        await graph_ingest.ingest_slice(_brain.get_instance(), registry.episodic_memory,
                                        limit=int(cfg.get("graph_ingest_per_tick", 100)))
        await graph_ingest.maintain_slice(_brain.get_instance(), pairs_limit=int(cfg.get("graph_pairs_per_tick", 200)),
                                          fade_limit=int(cfg.get("graph_fade_per_tick", 500)),
                                          hub_fraction=float(cfg.get("graph_hub_fraction", 0.15)),
                                          half_life_days=float(cfg.get("graph_half_life_days", 14.0)))
    return tick


async def _facts(registry) -> dict:
    """The L1 facts for the sleep's understanding; {} when there are none or they cannot be read."""
    try:
        layers = getattr(registry, "memory_layers", None)
        return dict(await layers.get_identity_and_facts()) if layers is not None else {}
    except Exception as exc:  # noqa: BLE001 - the backfill goes on without them
        log.warning("brain: L1 facts unavailable to the sleep: %s: %s", type(exc).__name__, exc)
        return {}


def _mind_tick(registry):
    """The existing L3 history encoded through the same understanding as new turns (brain/mind_sleep.py), a slice
    per tick, or None unless [mind] is on; before it, the L1 facts without a kind are classified (P1). The slice
    (minutes of LLM calls) runs as its own background task (R33):
    the tick only starts it when none is running and returns at once, so the scheduler, brain and graph parts of
    the sleep are never delayed by it."""
    if not _brain.mind_on():
        return None
    from brain import mind_facts, mind_sleep
    running: list[asyncio.Task] = []

    async def _slice() -> int:
        """The L1 facts not classified yet get their kind (one LLM call, P1), then a backfill slice whose gists
        name the user by L1 ``user_name`` (P3)."""
        facts = await _facts(registry)
        if facts:
            await mind_facts.classify(_brain.get_instance(), registry.llm_client, facts)
        return await mind_sleep.encode_slice(_brain.get_instance(), registry.episodic_memory, registry.llm_client,
                                             int(_brain.MIND["encode_per_tick"]), user_name=facts.get("user_name"))

    def _done(task: asyncio.Task) -> None:
        if not task.cancelled() and task.exception() is not None:
            exc = task.exception()
            log.warning("brain: backfill slice failed: %s: %s", type(exc).__name__, exc)

    async def tick() -> None:
        if running and not running[0].done():
            return
        running[:] = [asyncio.create_task(_slice())]
        running[0].add_done_callback(_done)
    tick.running = running  # the current backfill task, for shutdown and tests
    return tick


def _join(*ticks):
    """One on_tick from several: each runs in turn, one failing never stops the others (logged)."""
    live = [t for t in ticks if t is not None]
    if not live:
        return None

    async def tick() -> None:
        for t in live:
            try:
                await t()
            except Exception as exc:  # noqa: BLE001
                log.warning("brain: tick part %s failed: %s: %s", getattr(t, "__qualname__", t), type(exc).__name__, exc)
    return tick


def post_init_hook(registry: "ServiceRegistry", chat_id: str):
    tasks: list[asyncio.Task] = []
    backfills: list = []  # the mind tick, whose ``running`` holds the backfill task in progress

    async def _post_init(application: "Application") -> None:
        if not _brain.is_enabled():
            log.info("brain: disabled via config, sleep not started")
            return
        if not chat_id:
            log.warning("brain: no admin chat configured, sleep not started")
            return
        try:
            tick = _scheduler_tick(getattr(registry, "memory_layers", None))
            if tick is not None:  # a message left half-sent by the previous process goes out again
                recovered = await _scheduler.recover(_brain.get_instance())
                log.info("brain: scheduler on, recovered=%d", recovered)
            mind = _mind_tick(registry)
            backfills[:] = [mind] if mind is not None else []
            task = _brain.start(registry.working_memory, [chat_id], on_tick=_join(tick, _graph_tick(registry), mind))
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
        for t in [t for m in backfills for t in m.running if not t.done()]:
            t.cancel()  # an encode already in its thread finishes its write; the slice stops between episodes
            await asyncio.gather(t, return_exceptions=True)

    return _post_init, _post_shutdown
