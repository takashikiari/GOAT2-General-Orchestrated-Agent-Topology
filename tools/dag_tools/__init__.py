"""tools.dag_tools — per-task DAG communication tools for agents.

These tools are NOT module-level constants. They are built per DAG task via
``build_channel_tools(channel, task_id)`` so each task gets handlers bound
to its own channel and task_id.  ``workflow.agent_node.make_runner`` calls
this function when the DAG injects a channel into the execution context.

Exported tools (injected at runtime, not importable as constants):

    dag_push_update  — agent pushes a progress update to the orchestrator
    dag_check_inbox  — agent reads a message sent by the orchestrator
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from tools.types import AgentTool

if TYPE_CHECKING:
    from workflow.dag_channel import DagChannel


def build_channel_tools(channel: "DagChannel", task_id: str) -> list[AgentTool]:
    """Build agent tools bound to a specific DagChannel and task_id.

    Args:
        channel: The DagChannel for the current DAG run.
        task_id: Current task ID — prepended to outbox messages so the
            orchestrator knows which node sent the update.

    Returns:
        Two AgentTool instances: ``dag_push_update`` and ``dag_check_inbox``.
    """

    async def _push_update(message: str) -> str:
        payload = f"[{task_id}] {message}"
        await channel.push_outbox(payload)
        return f"Update sent to orchestrator: {payload}"

    async def _check_inbox() -> str:
        msg = await channel.pop_inbox(timeout=0)
        return msg if msg is not None else "(no messages in inbox)"

    return [
        AgentTool(
            name="dag_push_update",
            description=(
                "Push a progress update or partial result to the orchestrator. "
                "Use this to report findings, flag blockers, or share intermediate "
                "results before your task completes. The orchestrator can read these "
                "via workflow_status()."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Update text to send to the orchestrator.",
                    },
                },
                "required": ["message"],
            },
            handler=_push_update,
        ),
        AgentTool(
            name="dag_check_inbox",
            description=(
                "Check for a message from the orchestrator (non-blocking). "
                "Returns the next message if one was sent via workflow_send(), "
                "or '(no messages in inbox)' if the queue is empty. "
                "Use to receive mid-task instructions or clarifications."
            ),
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
            },
            handler=_check_inbox,
        ),
    ]
