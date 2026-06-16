"""DAG monitor/control/spawn tools — wired into GOAT's CORE_TOOLS.

These ``ToolDefinition`` objects let GOAT inspect, steer, and spawn
sub-DAGs during a direct conversational reply. They are built via a
factory so the ``MemoryManager`` reference, ``goat_session_id``, and
optional ``supervisor`` handle are captured as closures — no global
state, no singletons.

The four tools:

- ``query_dag_status(session_id)`` — reads ``dag:<sid>:progress`` and
  returns a JSON status string.
- ``control_dag(session_id, action)`` — sends ``pause`` / ``resume``
  / ``stop`` to ``dag:<sid>:control``; pause blocks the next wave,
  stop terminates after the current wave and surfaces a partial
  result.
- ``start_dag(task_description, session_id=None)`` — when a
  ``supervisor`` handle is provided, spawns the DAG IMMEDIATELY as a
  detached background task (GOAT never blocks). Otherwise falls back
  to writing a ``pending_dag`` signal that the supervisor picks up at
  the start of the next turn. Returns the session_id so GOAT can
  track the sub-DAG via ``query_dag_status``.
- ``list_dag_sessions()`` — scans ``dag:*:progress`` keys and returns
  a JSON array of active session records.

This module lives under ``tools/dag/`` so the tool surface is
discoverable alongside the other tool packages
(``tools/file``, ``tools/web``, ``tools/system``, ``tools/goat_skills``,
``tools/memory``). The legacy import path
``supervisor.pipeline.dag_tools`` re-exports ``make_dag_tools`` for
backward compatibility.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from memory.shared import MemoryManager
    from agents.base_agent import ToolDefinition

log = logging.getLogger("goat2.tools.dag")

# Re-export the canonical DAG execution surface. The shim modules
# supervisor.pipeline.dag_background and supervisor.pipeline.dag_execution
# re-export the same names for backward compatibility, but new code should
# import them from here.
from tools.dag.background import spawn, collect_finished, write_completion
from tools.dag.execution import run_dag_pipeline

__all__ = [
    "make_dag_tools",
    "spawn",
    "collect_finished",
    "write_completion",
    "run_dag_pipeline",
]


def make_dag_tools(
    mm: "MemoryManager | None",
    goat_session_id: str = "",
    supervisor=None,
) -> "list[ToolDefinition]":
    """Build DAG monitor/control/spawn tools with closures over mm, goat_session_id, supervisor.

    Args:
        mm: MemoryManager for Redis access (may be None — tools degrade gracefully).
        goat_session_id: GOAT's own session ID, used by start_dag to align
                         instruction keys when no explicit session_id is given.
        supervisor: Optional GoatSupervisor reference. When provided,
                    ``start_dag`` spawns the sub-DAG immediately
                    (detached, non-blocking). When None, ``start_dag``
                    falls back to writing a ``pending_dag`` signal that
                    the next turn picks up.

    Returns:
        List of four ToolDefinition objects: query_dag_status,
        control_dag, start_dag, list_dag_sessions.
    """
    from tools._make_tool import make_tool

    async def _query_dag_status(session_id: str) -> str:
        """Read dag:<session_id>:progress and return JSON status string."""
        if mm is None:
            return "no memory manager available"
        try:
            from config.roles import SESSION_ROLE
            key = f"dag:{session_id}:progress"
            record = await mm.working.backend.get(SESSION_ROLE, key)
            if record is None:
                return "no progress data"
            content = record.get("content", "")
            return content if content else "no progress data"
        except Exception as e:
            log.debug("query_dag_status failed: %s", e)
            return f"error reading progress: {e}"

    async def _control_dag(session_id: str, action: str) -> str:
        """Send pause|resume|stop to dag:<session_id>:control."""
        action = action.strip().lower()
        if action not in ("pause", "resume", "stop"):
            return f"invalid action '{action}': use pause, resume, or stop"
        ctrl_value = "run" if action == "resume" else action
        if mm is None:
            return "no memory manager available"
        from supervisor.pipeline.dag_control import write_dag_control
        ok = await write_dag_control(mm, session_id, ctrl_value)
        if ok:
            return f"dag {action} sent to session {session_id}"
        return f"failed to send {action} to session {session_id}"

    async def _start_dag(task_description: str, session_id: str | None = None) -> str:
        """Spawn a sub-DAG immediately (when supervisor is wired) or write a pending_dag signal.

        Returns the session_id so GOAT can track the sub-DAG. The DAG
        runs detached; GOAT never blocks. Multiple concurrent sub-DAGs
        are tracked by the supervisor's ``_active_dag_tasks`` dict.
        """
        if mm is None:
            return "no memory manager available"
        try:
            from config.roles import SESSION_ROLE
            from config.limits import WORKING_MEMORY_TTL
            # Prefer goat_session_id so run_dag_pipeline finds instructions at
            # dag:<self._session_id>:instructions — the key it always reads from.
            new_sid = session_id or goat_session_id or str(uuid.uuid4())
            key = f"dag:{new_sid}:instructions"
            await mm.working.store(SESSION_ROLE, key, task_description, ttl=WORKING_MEMORY_TTL)
            log.debug("start_dag: wrote instructions session=%s", new_sid)
            if supervisor is not None:
                # Immediate spawn — no pending_dag middleman.
                from tools.dag.background import spawn
                spawn(supervisor, task_description, new_sid)
                log.info("start_dag: spawned background DAG session=%s", new_sid)
            elif goat_session_id:
                # Fallback when no supervisor reference is available.
                pending_key = f"goat:{goat_session_id}:pending_dag"
                await mm.working.store(SESSION_ROLE, pending_key, new_sid, ttl=WORKING_MEMORY_TTL)
            return new_sid
        except Exception as e:
            log.debug("start_dag failed: %s", e)
            return f"error starting DAG: {e}"

    async def _list_dag_sessions() -> str:
        """Scan working memory for dag:*:progress keys and return active session list."""
        if mm is None:
            return "no memory manager available"
        try:
            from config.roles import SESSION_ROLE
            try:
                keys = await mm.working.backend.scan(SESSION_ROLE, "dag:*:progress")
            except AttributeError:
                return "scan not supported by current backend"
            active: list[dict] = []
            for key in keys[:10]:
                record = await mm.working.backend.get(SESSION_ROLE, key)
                if not record:
                    continue
                content = record.get("content", "")
                if not isinstance(content, str) or not content:
                    continue
                try:
                    payload = json.loads(content)
                    parts = str(key).split(":")
                    # Key format: goat2:working:user_session:dag:<session_id>:progress
                    payload["session_id"] = parts[4] if len(parts) > 4 else parts[-2] if len(parts) > 1 else "?"
                    active.append(payload)
                except Exception:
                    continue
            return json.dumps(active, ensure_ascii=False) if active else "no active DAG sessions"
        except Exception as e:
            log.debug("list_dag_sessions failed: %s", e)
            return f"error listing sessions: {e}"

    query_tool = make_tool(
        name="query_dag_status",
        description=(
            "Read the current progress of a running DAG session. "
            "Returns a JSON string with wave, total_waves, completed_tasks, and status."
        ),
        parameters={
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "The DAG session ID to query.",
                },
            },
            "required": ["session_id"],
        },
        handler=_query_dag_status,
    )

    control_tool = make_tool(
        name="control_dag",
        description=(
            "Send a lifecycle command to a running DAG session. "
            "action must be 'pause', 'resume', or 'stop'."
        ),
        parameters={
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "The DAG session ID to control.",
                },
                "action": {
                    "type": "string",
                    "enum": ["pause", "resume", "stop"],
                    "description": "Lifecycle action: pause, resume, or stop.",
                },
            },
            "required": ["session_id", "action"],
        },
        handler=_control_dag,
    )

    start_dag_tool = make_tool(
        name="start_dag",
        description=(
            "Schedule a multi-agent DAG pipeline to run after this conversational turn. "
            "Writes the task description to working memory as DAG instructions and returns "
            "the session_id. The DAG executes automatically after GOAT's reply."
        ),
        parameters={
            "type": "object",
            "properties": {
                "task_description": {
                    "type": "string",
                    "description": "Full description of the task for the DAG pipeline.",
                },
                "session_id": {
                    "type": "string",
                    "description": "Optional specific session_id to use (default: auto-generated).",
                },
            },
            "required": ["task_description"],
        },
        handler=_start_dag,
    )

    list_sessions_tool = make_tool(
        name="list_dag_sessions",
        description=(
            "List all active DAG sessions currently running in working memory. "
            "Returns a JSON array of session objects with wave, total_waves, and status."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=_list_dag_sessions,
    )

    return [query_tool, control_tool, start_dag_tool, list_sessions_tool]
