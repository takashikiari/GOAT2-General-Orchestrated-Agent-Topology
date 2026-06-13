"""DAG tools for GOAT CONVERSATIONAL — query, control, spawn, and list DAG sessions.

These ToolDefinition objects are created via a factory so the memory_manager
and goat_session_id are captured as closures, avoiding any need to modify
tool_runner.py dispatch.

GOAT calls these during conversational turns to monitor, steer, or spawn DAGs:
  - query_dag_status(session_id)  → reads dag:<session_id>:progress
  - control_dag(session_id, action) → writes dag:<session_id>:control
  - start_dag(task_description, session_id?) → writes instructions, signals supervisor
  - list_dag_sessions()           → scans dag:*:progress keys, returns JSON list
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from memory.shared import MemoryManager
    from agents.base_agent import ToolDefinition

log = logging.getLogger("goat2.supervisor.pipeline.dag_tools")

__all__ = ["make_dag_tools"]


def make_dag_tools(
    mm: "MemoryManager | None",
    goat_session_id: str = "",
) -> "list[ToolDefinition]":
    """Build DAG monitor/control/spawn tools with closures over mm and goat_session_id.

    Args:
        mm: MemoryManager for Redis access (may be None — tools degrade gracefully).
        goat_session_id: GOAT's own session ID, used by start_dag to write the
                         pending_dag signal and to align instruction keys.

    Returns:
        List of four ToolDefinition objects:
        query_dag_status, control_dag, start_dag, list_dag_sessions.
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
        """Write DAG instructions to working memory and signal the supervisor to fire the DAG."""
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
            if goat_session_id:
                pending_key = f"goat:{goat_session_id}:pending_dag"
                await mm.working.store(SESSION_ROLE, pending_key, new_sid, ttl=WORKING_MEMORY_TTL)
                log.debug(
                    "start_dag: pending_dag written goat=%s dag=%s",
                    goat_session_id, new_sid,
                )
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
