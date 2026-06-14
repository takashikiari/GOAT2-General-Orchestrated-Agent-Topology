"""Routing & pending-DAG working-memory state for GoatSupervisor.

Pure functions over the working-memory tier (Redis) that read and write the
small per-session control keys GOAT uses to route turns: the pending-DAG
handoff written by the ``start_dag`` tool, and the previous-routing decision
used to detect when the user disagrees with GOAT's last call. Extracted from
GoatSupervisor so the supervisor class stays focused on orchestration.

No singletons — every function receives its dependencies (memory_manager,
session_id, registry) explicitly. All functions degrade quietly on error so
routing state never hard-blocks a turn.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from supervisor.classification.classifier import IntentDepth

if TYPE_CHECKING:
    from memory.shared import MemoryManager
    from config.registry import ServiceRegistry

log = logging.getLogger("goat2.supervisor.session.routing_state")

__all__ = [
    "pop_pending_dag",
    "get_previous_routing",
    "set_previous_routing",
    "clear_previous_routing",
    "store_routing_correction",
]


async def pop_pending_dag(mm: "MemoryManager | None", session_id: str) -> str | None:
    """Read and delete goat:<session_id>:pending_dag from working memory."""
    if not mm:
        return None
    try:
        from config.roles import SESSION_ROLE as _SROLE
        key = f"goat:{session_id}:pending_dag"
        record = await mm.working.backend.get(_SROLE, key)
        if record is None:
            return None
        await mm.working.backend.delete(_SROLE, key)
        return record.get("content")
    except Exception as e:
        log.debug("pop_pending_dag failed: %s", e)
        return None


async def get_previous_routing(mm: "MemoryManager | None", session_id: str) -> str | None:
    """Read the previous routing decision from working memory."""
    if not mm:
        return None
    try:
        from config.roles import SESSION_ROLE as _SROLE
        key = f"goat:{session_id}:last_routing"
        record = await mm.working.backend.get(_SROLE, key)
        if record is None:
            return None
        return record.get("content")
    except Exception as e:
        log.debug("get_previous_routing failed: %s", e)
        return None


async def set_previous_routing(
    mm: "MemoryManager | None", session_id: str, depth: IntentDepth
) -> None:
    """Store the routing decision for the next turn to check against."""
    if not mm:
        return
    try:
        from config.roles import SESSION_ROLE as _SROLE
        from config.limits import WORKING_MEMORY_TTL
        key = f"goat:{session_id}:last_routing"
        now = time.time()
        record = {
            "id": key,
            "agent_role": _SROLE,
            "key": key,
            "content": depth.value,
            "metadata": {"type": "routing_decision"},
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            "created_at_ts": now,
            "expires_at": now + WORKING_MEMORY_TTL,
        }
        await mm.working.backend.set(_SROLE, key, record, expires_at=record["expires_at"])
    except Exception as e:
        log.debug("set_previous_routing failed: %s", e)


async def clear_previous_routing(mm: "MemoryManager | None", session_id: str) -> None:
    """Clear the previous routing decision."""
    if not mm:
        return
    try:
        from config.roles import SESSION_ROLE as _SROLE
        key = f"goat:{session_id}:last_routing"
        await mm.working.backend.delete(_SROLE, key)
    except Exception:
        pass


async def store_routing_correction(
    registry: "ServiceRegistry", intent: str, goat_routed: str, user_wanted: str
) -> None:
    """Store a routing correction for behavioral learning."""
    from supervisor.pipeline.behavioral_learning import store_correction
    await store_correction(registry, intent, goat_routed, user_wanted)
