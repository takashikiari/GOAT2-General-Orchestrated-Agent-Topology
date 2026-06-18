"""Per-turn style refresh — keep ``_behavior_style`` in sync with
the Letta ``persona`` block across the session.

Pure orchestration: ``refresh_style(supervisor)`` is the single
entry point. It re-reads the Letta block via ``behavior.store``
and updates the supervisor's in-memory cache. No LLM, no regex,
no I/O of its own (only the load_style call into Letta).

USAGE (from ``session.turn_persistence``):
    from supervisor.mechanisms.style_sync import refresh_style

    await refresh_style(supervisor)   # updates supervisor._behavior_style

WHY IT EXISTS:
    Without this, ``_behavior_style`` is refreshed only at session
    end. The mid-session style analysis can write a new profile to
    Letta, but the supervisor's in-memory cache stays stale, so the
    system prompt sees yesterday's profile. That delay was one of
    three reinforcing feedback loops causing GOAT to repeat itself.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from supervisor.supervisor import GoatSupervisor

log = logging.getLogger("goat2.supervisor.mechanisms.style_sync")

__all__ = ["refresh_style"]


async def refresh_style(supervisor: "GoatSupervisor") -> bool:
    """Re-read Letta's ``persona`` block into supervisor._behavior_style.

    Args:
        supervisor: The GoatSupervisor whose ``_behavior_style``
            attribute will be updated in place when the freshly
            loaded text differs from the cached value.

    Returns:
        True when the cache was updated; False when the load
        failed, returned empty text, or matched the current value.
        Best-effort — never raises.

    Notes:
        Defensive: any exception (mm is None, Letta unreachable,
        attribute missing) is logged at DEBUG and swallowed.
    """
    try:
        # Lazy import — behavior.store is part of the supervisor
        # package; loading it at module import time would create a
        # cycle through mechanisms → behavior → supervisor.
        from supervisor.behavior.store import load_style
        mm = getattr(supervisor, "memory_manager", None)
        fresh = await load_style(mm)
        if not fresh:
            return False
        current = getattr(supervisor, "_behavior_style", "")
        if fresh == current:
            return False
        log.debug("refresh_style: style updated (%d chars)", len(fresh))
        supervisor._behavior_style = fresh
        return True
    except Exception as exc:  # noqa: BLE001 — refresh is best-effort
        log.debug("refresh_style: failed — %s", exc)
        return False
