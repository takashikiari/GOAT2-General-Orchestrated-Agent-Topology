"""Background-task drain — finalize pending promotion work at shutdown.

Extracted from ``supervisor.py`` to keep that file under the
260-line ceiling. Owns the bounded-timeout drain of
``GoatSupervisor._background_tasks``.

USAGE:
    from supervisor.background_drain import drain_background_tasks
    await drain_background_tasks(supervisor, timeout_s=5.0)

BUG-027 fix: the supervisor previously spawned promotion tasks
with ``asyncio.create_task(...)`` and never awaited them —
exceptions were lost and a fast shutdown could drop in-flight
work. This module provides the drain that ``finalize_session``
calls so the CLI shutdown path waits for pending work.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from supervisor.supervisor import GoatSupervisor

log = logging.getLogger("goat2.supervisor.background_drain")

__all__ = ["drain_background_tasks", "DEFAULT_DRAIN_TIMEOUT_S"]


DEFAULT_DRAIN_TIMEOUT_S: float = 5.0


async def drain_background_tasks(
    supervisor: "GoatSupervisor",
    *,
    timeout_s: float = DEFAULT_DRAIN_TIMEOUT_S,
) -> None:
    """Await all tracked background tasks with a bounded timeout.

    Drains ``supervisor._background_tasks`` so the supervisor's
    shutdown path waits for pending work without blocking forever.
    Tasks that don't complete within ``timeout_s`` are cancelled
    and the supervisor proceeds with shutdown.

    Args:
        supervisor: The live GoatSupervisor (reads ``_background_tasks``).
        timeout_s: Wall-clock budget for the entire drain. Default 5s.
    """
    tasks_dict = getattr(supervisor, "_background_tasks", None)
    if not tasks_dict:
        return
    pending = list(tasks_dict.items())
    log.debug(
        "drain_background_tasks: draining %d task(s) (timeout=%.1fs)",
        len(pending), timeout_s,
    )
    try:
        await asyncio.wait_for(
            asyncio.gather(
                *(t for _, t in pending),
                return_exceptions=True,
            ),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        log.warning(
            "drain_background_tasks: drain timed out after %.1fs — "
            "cancelling %d pending task(s)",
            timeout_s, len(tasks_dict),
        )
        for _, t in pending:
            if not t.done():
                t.cancel()
    finally:
        # Task wrappers remove themselves on completion; any
        # stragglers (cancelled or never-started) are evicted here.
        for key, _ in pending:
            tasks_dict.pop(key, None)