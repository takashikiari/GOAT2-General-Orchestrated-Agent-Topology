"""DAG progress writer — emits per-wave progress to working memory.

After every wave of the DAG completes, the supervisor writes a
progress record to working memory at `dag:<session_id>:progress`.
GOAT reads this on demand via the `query_dag_status` tool or
`memory_get` with the same key. The progress key is overwritten
in place — no append-only log, no versioning.

The payload contains:
  - session_id:    the DAG session ID
  - wave:          1-indexed current wave number
  - total_waves:   total number of waves in this DAG
  - completed_tasks: list of task IDs that finished without error
  - status:        "running" (intermediate) or "complete" (terminal)
  - ts:            wall-clock timestamp

TTL is 3600s, matching the rest of the DAG output keys.
"""
from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from memory.shared import MemoryManager

log = logging.getLogger("goat2.supervisor.classification.dag_progress")

__all__ = ["write_wave_progress", "write_final_progress"]


def _build_progress_record(
    session_id: str,
    wave: int,
    total_waves: int,
    completed: list[str],
    status: str,
) -> tuple[str, dict[str, Any]]:
    """Build the (key, record) tuple for a progress write.

    Centralized so `write_wave_progress` and `write_final_progress`
    produce identical record shapes.
    """
    from config.limits import DAG_RESULT_TTL
    from config.roles import SESSION_ROLE
    payload = {
        "session_id": session_id,
        "wave": wave,
        "total_waves": total_waves,
        "completed_tasks": completed,
        "status": status,
        "ts": time.time(),
    }
    key = f"dag:{session_id}:progress"
    now = time.time()
    record = {
        "id": key,
        "agent_role": SESSION_ROLE,
        "key": key,
        "content": json.dumps(payload, ensure_ascii=False),
        "metadata": {"type": "dag_progress", "session_id": session_id},
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "created_at_ts": now,
        "expires_at": now + DAG_RESULT_TTL,
    }
    return key, record


async def write_wave_progress(
    memory_manager: "MemoryManager | None",
    session_id: str | None,
    wave: int,
    total_waves: int,
    completed: list[str],
) -> bool:
    """Write an in-flight progress record to working memory.

    Called after every wave finishes. Best-effort: returns False
    on any backend error and logs at DEBUG level. Never raises.
    """
    if not (memory_manager and session_id):
        return False
    try:
        key, record = _build_progress_record(
            session_id, wave, total_waves, completed, "running",
        )
        from config.roles import SESSION_ROLE
        await memory_manager.working.backend.set(
            SESSION_ROLE, key, record, expires_at=record["expires_at"],
        )
        log.debug(
            "dag:%s:progress written — wave %d/%d, %d completed",
            session_id, wave, total_waves, len(completed),
        )
        return True
    except Exception as e:
        log.debug("write_wave_progress failed: %s", e)
        return False


async def write_final_progress(
    memory_manager: "MemoryManager | None",
    session_id: str | None,
    total_waves: int,
    completed: list[str],
) -> bool:
    """Mark the progress record as `complete` after the final wave.

    Called once at the end of `WorkflowGraph.execute()`, just
    before the final result is written. Best-effort, never raises.
    """
    if not (memory_manager and session_id):
        return False
    try:
        key, record = _build_progress_record(
            session_id, total_waves, total_waves, completed, "complete",
        )
        from config.roles import SESSION_ROLE
        await memory_manager.working.backend.set(
            SESSION_ROLE, key, record, expires_at=record["expires_at"],
        )
        log.info("dag:%s:progress marked complete (%d/%d waves)",
                 session_id, total_waves, total_waves)
        return True
    except Exception as e:
        log.debug("write_final_progress failed: %s", e)
        return False
