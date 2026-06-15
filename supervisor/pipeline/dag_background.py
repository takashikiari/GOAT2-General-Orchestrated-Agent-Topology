"""Detached background DAG execution — GOAT is the kernel, DAG is a background process.

When GOAT decides to run a DAG, the supervisor spawns it as a detached asyncio task
instead of awaiting it, so ``sv.run()`` returns immediately and GOAT stays responsive.
The DAG runs independently and writes its status/result to working memory; GOAT reads
working memory to report status and completion on later turns. The DAG never blocks GOAT.

All functions take the live ``supervisor`` (no singletons, no module state). Working
memory is the only shared channel — the DAG writes, GOAT reads.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from config.roles import SESSION_ROLE

if TYPE_CHECKING:
    from supervisor.supervisor import GoatSupervisor
    from memory.shared import MemoryManager

log = logging.getLogger("goat2.supervisor.pipeline.dag_background")

__all__ = ["spawn", "write_completion", "collect_finished", "status"]

# Per-call budget for reading a finished DAG's result from working memory.
# Kept small so a slow Redis call cannot stall GOAT.
_REDIS_READ_TIMEOUT_S: float = 1.0

# After a DAG completes, wait this long before auto-cleaning its `dag:*` keys.
# The delay lets GOAT's next turn read the result/control keys before they
# disappear; 60s is plenty for a single user-facing turn.
_AUTO_CLEAN_DELAY_S: float = 60.0


def spawn(supervisor: "GoatSupervisor", dag_instructions: str, session_id: str) -> "asyncio.Task":
    """Spawn the DAG as a detached background task and track it; return the Task.

    The task is stored in ``supervisor._active_dag_tasks[session_id]`` so GOAT can
    later check whether it is still running and surface its result.

    NOTE: instructions are written to working memory by ``_dispatch()`` (in supervisor.py)
    *before* this function is called, using the full ``mem_ctx`` and capabilities summary.
    We must NOT write them again here — a second write would overwrite the richer data with
    empty ``mem_ctx`` and empty ``capabilities``.  ``run_dag_pipeline`` falls back to the
    ``dag_instructions`` parameter when the memory key is absent.
    """
    task = asyncio.create_task(_dag_runner(supervisor, session_id, dag_instructions))
    supervisor._active_dag_tasks[session_id] = task
    log.info("spawn: background DAG session=%s (active=%d)", session_id, len(supervisor._active_dag_tasks))
    return task


async def _dag_runner(supervisor: "GoatSupervisor", session_id: str, dag_instructions: str) -> str:
    """Run the DAG pipeline detached; persist running/complete status + result.

    Reuses ``run_dag_pipeline`` unchanged (DagBridge/GoatValidator run inside it).
    Any failure is captured as the completion summary so GOAT can report it.
    """
    mm = supervisor.memory_manager
    await _write(mm, f"dag:{session_id}:status", "running")
    t0 = time.monotonic()
    try:
        from supervisor.pipeline.dag_execution import run_dag_pipeline
        # Pass dag_instructions as intent (fallback); run_dag_pipeline reads the
        # richer structured instructions from working memory first (set by _dispatch()).
        result = await run_dag_pipeline(supervisor, dag_instructions, t0, "")
        summary = (result.summary or "").strip() or "DAG finished with no summary."
        log.info("_dag_runner: session=%s complete (%.1fs)", session_id, time.monotonic() - t0)
    except Exception as exc:
        summary = f"DAG failed: {exc}"
        log.warning("_dag_runner: session=%s failed: %s", session_id, exc)
    await write_completion(mm, session_id, summary)
    return summary


async def write_completion(mm: "MemoryManager | None", session_id: str, summary: str) -> None:
    """Write the DAG's final result and 'complete' status to working memory.

    After the result is persisted, schedules a detached auto-clean task that
    removes every ``dag:<session_id>:*`` key from working memory after a
    short delay. The delay lets GOAT's next turn read the result first.
    """
    await _write(mm, f"dag:{session_id}:result", summary)
    await _write(mm, f"dag:{session_id}:status", "complete")
    log.debug("write_completion: session=%s summary=%.80s", session_id, summary)
    # Detached auto-clean — never blocks the caller. Failures are swallowed.
    if mm is not None:
        try:
            asyncio.create_task(_auto_clean_dag(session_id, mm))
        except RuntimeError as exc:
            log.debug("write_completion: cannot schedule auto-clean (no loop?): %s", exc)


async def _auto_clean_dag(session_id: str, mm: "MemoryManager") -> None:
    """Sleep, then remove every ``dag:<session_id>:*`` key from working memory.

    Runs detached — never blocks the supervisor. After ``_AUTO_CLEAN_DELAY_S``
    (default 60s) it lists the working namespace, filters to keys scoped to
    this session, and deletes them one by one. Failures on individual keys
    are swallowed so one bad key cannot leave the rest behind.
    """
    try:
        await asyncio.sleep(_AUTO_CLEAN_DELAY_S)
        backend = getattr(getattr(mm, "working", None), "backend", None)
        if backend is None:
            log.debug("auto_clean_dag: no working backend (session=%s)", session_id)
            return
        prefix = f"dag:{session_id}:"
        try:
            keys = await backend.keys("user_session")
        except Exception as exc:  # noqa: BLE001
            log.warning("auto_clean_dag: keys() failed for session=%s: %s", session_id, exc)
            return
        dag_keys = [k for k in keys if str(k).startswith(prefix)]
        deleted = 0
        for key in dag_keys:
            try:
                await backend.delete("user_session", str(key))
                deleted += 1
            except Exception as exc:  # noqa: BLE001
                log.debug("auto_clean_dag: delete(%s) failed: %s", key, exc)
        log.info("auto_clean_dag: cleaned %d/%d keys for session=%s",
                 deleted, len(dag_keys), session_id)
    except Exception as exc:  # noqa: BLE001
        log.debug("auto_clean_dag: aborted for session=%s: %s", session_id, exc)


async def collect_finished(supervisor: "GoatSupervisor") -> str:
    """Surface finished background DAGs as a note and clear them from tracking.

    TRULY NON-BLOCKING: this function never awaits a still-running DAG task.
    It iterates ``_active_dag_tasks`` and only touches tasks whose ``task.done()``
    is already True (a synchronous, microsecond-level check). For each such
    finished task it reads the result from working memory with a short timeout
    so a single slow Redis call cannot stall GOAT. If no task is finished,
    the function returns ``''`` without performing any I/O.

    GOAT is the kernel and must respond immediately on every turn — even if
    a background DAG is still running, this function will return promptly
    and GOAT continues. Finished DAGs are surfaced as a ``[DAG Update]`` note
    in the next turn's context.
    """
    notes: list[str] = []
    # Fast path: synchronous inspection only. If nothing is finished, return ''.
    finished_sids: list[str] = []
    for sid, task in list(supervisor._active_dag_tasks.items()):
        if task.done():  # synchronous, never awaits the running DAG
            finished_sids.append(sid)
    if not finished_sids:
        return ""
    # Slow path: read results from working memory for finished tasks, with
    # a short timeout per read so a slow Redis call cannot stall GOAT.
    for sid in finished_sids:
        try:
            result = await asyncio.wait_for(
                _read(supervisor.memory_manager, f"dag:{sid}:result"),
                timeout=_REDIS_READ_TIMEOUT_S,
            ) or "completed"
        except asyncio.TimeoutError:
            log.warning("collect_finished: read timeout for session=%s", sid)
            result = "(result read timed out)"
        except Exception as exc:  # noqa: BLE001
            log.warning("collect_finished: read failed for session=%s: %s", sid, exc)
            result = "(result read failed)"
        notes.append(f"- session {sid}: {result}")
        # Remove from tracking *after* we have its result.
        supervisor._active_dag_tasks.pop(sid, None)
        log.info("collect_finished: surfaced and cleared session=%s", sid)
    return ("[DAG Update]\n" + "\n".join(notes)) if notes else ""


async def status(supervisor: "GoatSupervisor", session_id: str) -> dict:
    """Return ``{session_id, running, status, progress}`` for a background DAG.

    ``running`` comes from the task object; ``status``/``progress`` are read from
    working memory (the DAG's shared channel).
    """
    task = supervisor._active_dag_tasks.get(session_id)
    running = task is not None and not task.done()
    mm = supervisor.memory_manager
    st = await _read(mm, f"dag:{session_id}:status") or ("running" if running else "unknown")
    progress = await _read(mm, f"dag:{session_id}:progress")
    log.debug("status: session=%s running=%s status=%s", session_id, running, st)
    return {"session_id": session_id, "running": running, "status": st, "progress": progress}


async def _write(mm: "MemoryManager | None", key: str, content: str) -> None:
    """Best-effort working-memory write (DAG → working memory only)."""
    if mm is None:
        return
    try:
        from config.limits import WORKING_MEMORY_TTL
        await mm.working.store(SESSION_ROLE, key, content, ttl=WORKING_MEMORY_TTL)
    except Exception as exc:
        log.debug("_write(%s) failed: %s", key, exc)


async def _read(mm: "MemoryManager | None", key: str) -> str:
    """Best-effort working-memory read; returns the content string or ''."""
    if mm is None:
        return ""
    try:
        record = await mm.working.backend.get(SESSION_ROLE, key)
        return record.get("content", "") if record else ""
    except Exception as exc:
        log.debug("_read(%s) failed: %s", key, exc)
        return ""
