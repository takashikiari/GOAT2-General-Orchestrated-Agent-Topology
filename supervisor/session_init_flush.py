"""Session-initialization working-memory flush — non-blocking, fire-and-forget.

Used by GoatSupervisor to ensure a fresh session starts with a clean
working-memory state, even when Redis persisted data from a previous
process lifetime. The flush promotes every working-memory entry to the
episodic tier (via ``check_and_promote``) and leaves working memory
empty for the new session.

GOAT-LEVEL ONLY: the flush runs in a detached asyncio task so GOAT
continues responding to the user immediately. Any failure is logged
and swallowed; the next turn will retry.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config.registry import ServiceRegistry

log = logging.getLogger("goat2.supervisor.session_init_flush")

__all__ = ["schedule_working_memory_flush"]


def schedule_working_memory_flush(registry: "ServiceRegistry") -> None:
    """Fire-and-forget working-memory flush. Non-blocking.

    Schedules an asyncio task that calls ``check_and_promote`` with
    ``max_entries=0`` (every entry is eligible for promotion). Failures
    are logged at WARNING and swallowed. If called outside a running
    event loop, the call is a silent no-op.
    """
    async def _flush() -> None:
        mm = registry.memory_manager
        if mm is None:
            return
        try:
            from memory.working.capacity import check_and_promote
            await check_and_promote(
                mm.working.backend, mm.episodic, "user_session", max_entries=0,
            )
            log.info("session init flush: working memory promoted to episodic")
        except Exception as exc:  # noqa: BLE001
            log.warning("session init flush failed: %s", exc)
    try:
        asyncio.get_event_loop().create_task(_flush())
    except RuntimeError as exc:
        log.debug("session init flush: no event loop yet (%s)", exc)
