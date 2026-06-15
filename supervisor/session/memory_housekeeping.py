"""Memory housekeeping helpers — daemon lifecycle, per-turn GC, finalize.

Extracted from ``supervisor.py`` to keep that file under the 260-line
ceiling. Three free functions, all operating on the live
``GoatSupervisor`` instance passed in (no module-level state, no
singletons):

  - ``start_memory_daemon(supervisor)`` — lazy-construct + fire-and-forget
    start of the three-tier promotion daemon on the supervisor's
    ``MemoryManager`` / ``ServiceRegistry``.
  - ``stop_memory_daemon(supervisor)`` — awaitable shutdown; safe to call
    even when no daemon was started.
  - ``tick_gc(supervisor)`` — pure predicate wrapper that increments the
    supervisor's turn counter and fires a detached ``collect(...)`` task
    when the periodic condition is met.
  - ``finalize_memory(supervisor)`` — daemon stop + working→episodic
    final promote, called from ``GoatSupervisor.finalize_session``.

All I/O is non-blocking (the daemon is a detached ``asyncio.create_task``;
the GC is also a detached task). Errors are logged and swallowed — these
helpers must never break the GOAT turn path.
"""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("goat2.supervisor.session.memory_housekeeping")

__all__ = ["start_memory_daemon", "stop_memory_daemon", "tick_gc", "finalize_memory"]


def start_memory_daemon(supervisor) -> None:
    """Lazy-start the three-tier promotion daemon on ``supervisor``.

    Idempotent: if ``supervisor._memory_daemon`` is already set, this is
    a no-op. If construction or the ``create_task`` call raises, the
    supervisor's daemon attribute is reset to ``None`` so the next call
    is a fresh attempt. Never raises.
    """
    if getattr(supervisor, "_memory_daemon", None) is not None:
        return
    try:
        from memory.shared.memory_daemon import MemoryDaemon
        daemon = MemoryDaemon()
        supervisor._memory_daemon = daemon
        asyncio.create_task(
            daemon.start(supervisor.memory_manager, supervisor.registry),
            name="memory-daemon-start",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("GoatSupervisor: failed to start memory daemon: %s", exc)
        supervisor._memory_daemon = None


async def stop_memory_daemon(supervisor) -> None:
    """Await the daemon's clean shutdown; reset the supervisor slot.

    Safe to call when no daemon was ever started. Never raises.
    """
    daemon = getattr(supervisor, "_memory_daemon", None)
    if daemon is None:
        return
    try:
        await daemon.stop()
    except Exception as exc:  # noqa: BLE001
        log.warning("GoatSupervisor: memory daemon stop failed: %s", exc)
    supervisor._memory_daemon = None


def tick_gc(supervisor) -> None:
    """Per-turn hook: bump the supervisor's turn counter and maybe GC.

    Always increments ``supervisor._turn_counter`` (creating it on first
    call) so the supervisor's turn cadence is observable even when the
    memory manager is unavailable. Fires a detached ``collect(...)``
    task when ``schedule_auto_collect`` says it's time and the manager
    is reachable. Never raises.
    """
    counter = getattr(supervisor, "_turn_counter", 0) + 1
    supervisor._turn_counter = counter
    if not getattr(supervisor, "memory_manager", None):
        return
    try:
        from memory.working.garbage_collector import collect, schedule_auto_collect
        backend = getattr(supervisor.memory_manager.working, "backend", None)
        if backend is None:
            return
        if schedule_auto_collect(backend, "user_session", counter):
            log.debug("GoatSupervisor: scheduling GC sweep (turn=%d)", counter)
            asyncio.create_task(collect(backend, "user_session"), name="working-gc")
    except Exception as exc:  # noqa: BLE001
        log.debug("GoatSupervisor: GC schedule failed: %s", exc)


async def finalize_memory(supervisor) -> None:
    """End-of-session memory hygiene: stop daemon, final working→episodic promote.

    Mirrors the previous body of ``GoatSupervisor.finalize_session``'s
    memory half. ``finalize_behavior`` is still called by the supervisor
    itself — this helper only owns the memory tier.
    """
    await stop_memory_daemon(supervisor)
    if not getattr(supervisor, "memory_manager", None):
        return
    try:
        from memory.working.capacity import check_and_promote
        await check_and_promote(
            supervisor.memory_manager.working.backend,
            supervisor.memory_manager.episodic,
            "user_session", max_entries=100,
        )
        log.info("finalize_memory: working memory promoted to episodic")
    except Exception as exc:  # noqa: BLE001
        log.warning("finalize_memory: promote failed: %s", exc)
