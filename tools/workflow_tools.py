"""tools.workflow_tools — orchestrator tools for DAG workflow management.

Exposes four tools to the orchestrator's LLM:

    start_workflow   — build and launch a DAG from a structured node spec
    workflow_status  — read current DAG state from Redis
    workflow_send    — push a message to a running DAG's inbox
    stop_workflow    — cancel a running DAG

The orchestrator remains fully decoupled from the DAG engine: it only
interacts via these tool calls and the Redis channel.  No shared state.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from orchestrator.tools import ToolDefinition
from workflow.dag_channel import DagChannel
from workflow.dag_manager import DagManager
from workflow.errors import WorkflowError, WorkflowNotFound

if TYPE_CHECKING:
    pass

log = logging.getLogger("goat2.tools.workflow")

ChannelFactory = Callable[[str], DagChannel]


def build_workflow_tools(
    dag_manager: DagManager,
    channel_factory: ChannelFactory,
) -> list[ToolDefinition]:
    """Build the four workflow orchestrator tools.

    Args:
        dag_manager: The ``DagManager`` instance that owns active DAG tasks.
        channel_factory: Callable ``(dag_id) -> DagChannel`` for status reads.

    Returns:
        List of four ``ToolDefinition`` objects ready for the orchestrator.
    """
    return [
        _start_workflow_tool(dag_manager),
        _workflow_status_tool(channel_factory),
        _workflow_send_tool(channel_factory),
        _stop_workflow_tool(dag_manager),
    ]


# ── start_workflow ────────────────────────────────────────────────────────────

_START_SCHEMA = {
    "type": "object",
    "properties": {
        "nodes": {
            "type": "array",
            "description": (
                "Ordered list of agent node specs. Each node: "
                "{id, role, task, deps}. "
                "roles: planner / researcher / coder / critic / summarizer."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "id":   {"type": "string"},
                    "role": {"type": "string"},
                    "task": {"type": "string"},
                    "deps": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "role", "task"],
            },
        },
        "dag_id": {
            "type": "string",
            "description": "Optional DAG identifier (auto-generated if omitted).",
        },
        "initial_context": {
            "type": "object",
            "description": "Optional seed data available to all nodes.",
        },
    },
    "required": ["nodes"],
}


def _start_workflow_tool(dag_manager: DagManager) -> ToolDefinition:
    async def handler(
        nodes: list[dict],
        dag_id: str = "",
        initial_context: dict | None = None,
        chat_id: str = "",
    ) -> str:
        try:
            effective_id = dag_id.strip() or None
            graph = dag_manager.build_graph(
                nodes,
                dag_id=effective_id or "dag",
                use_stubs=False,
            )
            ctx = dict(initial_context or {})
            ctx.setdefault("chat_id", chat_id)
            launched_id = dag_manager.start(graph, ctx, dag_id=effective_id)
            return (
                f"✅ Workflow started — dag_id: {launched_id}\n"
                f"Nodes: {[n['id'] for n in nodes]}\n"
                f"Use workflow_status('{launched_id}') to track progress."
            )
        except (ValueError, ImportError) as exc:
            log.warning("start_workflow failed: %s", exc)
            return f"❌ Could not start workflow: {exc}"
        except WorkflowError as exc:
            log.warning("start_workflow WorkflowError: %s", exc)
            return f"❌ Workflow error: {exc}"

    return ToolDefinition(
        name="start_workflow",
        description=(
            "Launch a background multi-agent DAG workflow for complex tasks "
            "that require multiple specialist agents (planner, researcher, coder, "
            "critic, summarizer) working in sequence or parallel. "
            "Returns a dag_id you can use to track progress. "
            "Use only for substantial, multi-step tasks — not for simple queries."
        ),
        parameters=_START_SCHEMA,
        handler=handler,
    )


# ── workflow_status ───────────────────────────────────────────────────────────

def _workflow_status_tool(channel_factory: ChannelFactory) -> ToolDefinition:
    async def handler(dag_id: str, chat_id: str = "") -> str:
        channel = channel_factory(dag_id)
        try:
            status = await channel.get_status()
            if status is None:
                return f"No workflow found with dag_id '{dag_id}'."

            state = status.get("state", "unknown")
            node_states = status.get("node_states", {})
            lines = [f"DAG {dag_id} — state: {state}"]
            if node_states:
                for nid, nstate in node_states.items():
                    lines.append(f"  {nid}: {nstate}")

            if state in ("done", "failed"):
                result = await channel.get_result()
                if result:
                    lines.append("\nResults:")
                    for nid, val in result.get("results", {}).items():
                        preview = val[:120] + "…" if len(val) > 120 else val
                        lines.append(f"  {nid}: {preview}")
                    for nid, err in result.get("errors", {}).items():
                        lines.append(f"  {nid} ERROR: {err}")

            outbox = await channel.read_outbox(limit=5)
            if outbox:
                lines.append("\nRecent messages from DAG:")
                for msg in outbox:
                    lines.append(f"  • {msg}")

            return "\n".join(lines)
        finally:
            await channel.close()

    return ToolDefinition(
        name="workflow_status",
        description=(
            "Check the current status of a running or completed DAG workflow. "
            "Shows node-level state, results (on completion), and any messages "
            "the DAG has sent back."
        ),
        parameters={
            "type": "object",
            "properties": {
                "dag_id": {"type": "string", "description": "The DAG identifier."},
            },
            "required": ["dag_id"],
        },
        handler=handler,
    )


# ── workflow_send ─────────────────────────────────────────────────────────────

def _workflow_send_tool(channel_factory: ChannelFactory) -> ToolDefinition:
    async def handler(dag_id: str, message: str, chat_id: str = "") -> str:
        channel = channel_factory(dag_id)
        try:
            await channel.push_inbox(message)
            return f"✅ Message sent to workflow '{dag_id}'."
        except Exception as exc:
            return f"❌ Could not send message: {exc}"
        finally:
            await channel.close()

    return ToolDefinition(
        name="workflow_send",
        description=(
            "Send a message or instruction to a running DAG workflow via its inbox. "
            "Use this to provide additional context, clarification, or direction "
            "to a workflow that is waiting for input."
        ),
        parameters={
            "type": "object",
            "properties": {
                "dag_id":  {"type": "string"},
                "message": {"type": "string", "description": "Message to send to the DAG."},
            },
            "required": ["dag_id", "message"],
        },
        handler=handler,
    )


# ── stop_workflow ─────────────────────────────────────────────────────────────

def _stop_workflow_tool(dag_manager: DagManager) -> ToolDefinition:
    async def handler(dag_id: str, chat_id: str = "") -> str:
        try:
            await dag_manager.stop(dag_id)
            return f"✅ Workflow '{dag_id}' stopped."
        except WorkflowNotFound:
            return f"❌ No active workflow with dag_id '{dag_id}'."
        except Exception as exc:
            return f"❌ Could not stop workflow: {exc}"

    return ToolDefinition(
        name="stop_workflow",
        description=(
            "Cancel and stop a running DAG workflow. "
            "Use this when the user wants to abort a long-running task."
        ),
        parameters={
            "type": "object",
            "properties": {
                "dag_id": {"type": "string"},
            },
            "required": ["dag_id"],
        },
        handler=handler,
    )
