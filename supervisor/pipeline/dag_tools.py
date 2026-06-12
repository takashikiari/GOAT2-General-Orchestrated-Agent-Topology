"""DAG tools for GOAT CONVERSATIONAL — query_dag_status and control_dag.

These ToolDefinition objects are created via a factory so the memory_manager
is captured as a closure, avoiding any need to modify tool_runner.py dispatch.

GOAT calls these during conversational turns to monitor or steer a running DAG:
  - query_dag_status(session_id) → reads dag:<session_id>:progress
  - control_dag(session_id, action) → writes dag:<session_id>:control
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from memory.shared import MemoryManager
    from agents.base_agent import ToolDefinition

log = logging.getLogger("goat2.supervisor.pipeline.dag_tools")

__all__ = ["make_dag_tools"]


def make_dag_tools(mm: "MemoryManager | None") -> "list[ToolDefinition]":
    """Build DAG monitor/control tools with memory_manager captured in closure.

    Args:
        mm: MemoryManager for Redis access (may be None — tools degrade gracefully).

    Returns:
        List of two ToolDefinition objects: query_dag_status, control_dag.
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
        # "resume" maps to control value "run"
        ctrl_value = "run" if action == "resume" else action
        if mm is None:
            return "no memory manager available"
        from supervisor.pipeline.dag_control import write_dag_control
        ok = await write_dag_control(mm, session_id, ctrl_value)
        if ok:
            return f"dag {action} sent to session {session_id}"
        return f"failed to send {action} to session {session_id}"

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

    return [query_tool, control_tool]
