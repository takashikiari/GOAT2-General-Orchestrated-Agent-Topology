"""DAG control — GOAT reads/writes dag:<session_id>:control for lifecycle management.

Control key: dag:<session_id>:control
Values: "run" | "pause" | "stop"

GOAT writes the control key; WorkflowGraph reads it after every wave.
- "run" or missing → continue
- "pause" → wait up to _PAUSE_MAX_WAIT seconds (checking every _PAUSE_INTERVAL),
  then continue (or stop if control flips to "stop" while paused)
- "stop" → return False, caller terminates gracefully
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from memory.shared import MemoryManager

log = logging.getLogger("goat2.supervisor.pipeline.dag_control")

__all__ = ["write_dag_control", "read_dag_control", "wait_if_paused"]

_CONTROL_TTL: Final[int] = 3600
_PAUSE_INTERVAL: Final[float] = 2.0
_PAUSE_MAX_WAIT: Final[float] = 60.0


async def write_dag_control(
    mm: "MemoryManager | None",
    session_id: str,
    value: str,
) -> bool:
    """Write a control command to dag:<session_id>:control (TTL 3600s).

    Args:
        mm: MemoryManager for Redis access.
        session_id: Target DAG session.
        value: One of "run", "pause", "stop".

    Returns:
        True on success, False on any backend error.
    """
    if mm is None:
        return False
    try:
        from config.roles import SESSION_ROLE
        from memory.working.working_record import RecordDict
        key = f"dag:{session_id}:control"
        now = time.time()
        record: RecordDict = {
            "id": key,
            "agent_role": SESSION_ROLE,
            "key": key,
            "content": value,
            "metadata": {"type": "dag_control", "session_id": session_id},
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            "created_at_ts": now,
            "expires_at": now + _CONTROL_TTL,
        }
        await mm.working.backend.set(SESSION_ROLE, key, record, expires_at=record["expires_at"])
        log.info("dag_control: session=%s value=%s", session_id, value)
        return True
    except Exception as e:
        log.debug("write_dag_control failed: %s", e)
        return False


async def read_dag_control(
    mm: "MemoryManager | None",
    session_id: str,
) -> str | None:
    """Read the current control command from dag:<session_id>:control.

    Returns:
        Control string ("run", "pause", "stop") or None if not set.
    """
    if mm is None:
        return None
    try:
        from config.roles import SESSION_ROLE
        key = f"dag:{session_id}:control"
        record = await mm.working.backend.get(SESSION_ROLE, key)
        if record is None:
            return None
        return record.get("content")
    except Exception as e:
        log.debug("read_dag_control failed: %s", e)
        return None


async def wait_if_paused(
    mm: "MemoryManager | None",
    session_id: str | None,
) -> bool:
    """Check control key and handle pause/stop after a DAG wave.

    Called by WorkflowGraph after each wave. Returns True to continue,
    False to stop (caller must terminate and return current results).
    """
    if not (mm and session_id):
        return True
    ctrl = await read_dag_control(mm, session_id)
    if ctrl == "stop":
        log.info("DAG stop signal received: session=%s", session_id)
        return False
    if ctrl == "pause":
        waited = 0.0
        log.info("DAG paused: session=%s", session_id)
        while waited < _PAUSE_MAX_WAIT:
            await asyncio.sleep(_PAUSE_INTERVAL)
            waited += _PAUSE_INTERVAL
            ctrl = await read_dag_control(mm, session_id)
            if ctrl != "pause":
                break
        if ctrl == "stop":
            log.info("DAG stop after pause: session=%s waited=%.1fs", session_id, waited)
            return False
        log.info("DAG resumed: session=%s waited=%.1fs", session_id, waited)
    return True
