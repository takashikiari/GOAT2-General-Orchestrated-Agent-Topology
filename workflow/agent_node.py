"""workflow.agent_node — bridges agents/ templates to DAG NodeRunner callables.

A ``NodeRunner`` has signature ``async (task_id, context) -> Any``.
``make_runner`` wraps a ``BaseAgent.execute()`` call in that signature,
translating the DAG context dict into ``AgentTask`` / ``AgentResult``
objects that agents understand.

When the DAG injects a ``DagChannel`` into context (key ``__dag_channel__``),
the runner creates a FRESH agent instance and adds per-task communication
tools (``dag_push_update``, ``dag_check_inbox``) so the agent can interact
with the orchestrator mid-task without polluting the shared cached instance.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from workflow.routing import AgentRouter

if TYPE_CHECKING:
    from workflow.models import NodeRunner


def make_runner(
    role: str,
    task_description: str,
    router: AgentRouter,
) -> "NodeRunner":
    """Build a ``NodeRunner`` that delegates to the agent identified by ``role``.

    The runner:
    1. Checks context for an injected ``DagChannel`` (``__dag_channel__``).
    2. If a channel is present: creates a fresh agent instance and adds
       ``dag_push_update`` / ``dag_check_inbox`` tools bound to that channel.
    3. If no channel: uses the cached agent from the router (no overhead).
    4. Converts the DAG context into ``{dep_id: AgentResult}`` objects.
    5. Calls ``agent.execute(task, dep_context)`` and returns the string result.

    Args:
        role: Agent role name (e.g. ``"planner"``).
        task_description: Natural-language task prompt given to the agent.
        router: ``AgentRouter`` instance used to resolve the agent class.

    Returns:
        An async callable compatible with ``TaskNode.runner``.

    Raises:
        ValueError: At creation time if ``role`` is not registered.
        ImportError: At call time if ``config.agent_types`` is not importable.
    """
    if role not in AgentRouter.registered_roles():
        raise ValueError(f"Unknown agent role: {role!r}")

    async def _runner(task_id: str, context: dict[str, Any]) -> str:
        try:
            from config.agent_types import AgentResult, AgentTask
        except ImportError as exc:
            raise ImportError(
                "config.agent_types is required to run agent nodes. "
                f"Original error: {exc}"
            ) from exc

        channel = context.get("__dag_channel__")

        if channel is not None:
            # Fresh instance — dag_tools are task-specific, must not leak
            # to other concurrent tasks sharing the cached agent.
            agent = router.instantiate(role)
            from tools.dag_tools import build_channel_tools
            for t in build_channel_tools(channel, task_id):
                agent.add_tool(t.name, t.description, t.parameters, t.handler)
        else:
            agent = router.get(role)

        dep_context: dict[str, Any] = {}
        for dep_id, dep_value in context.items():
            if dep_id.startswith("__"):
                continue
            if hasattr(dep_value, "output"):
                dep_context[dep_id] = dep_value
            elif isinstance(dep_value, str):
                dep_context[dep_id] = AgentResult(role="unknown", output=dep_value)
            elif dep_value is not None:
                dep_context[dep_id] = AgentResult(role="unknown", output=str(dep_value))

        task = AgentTask(
            id=task_id,
            role=role,
            prompt=task_description,
            depends_on=list(dep_context.keys()),
        )
        return await agent.execute(task, dep_context)

    return _runner


def make_stub_runner(task_description: str) -> "NodeRunner":
    """Return a no-op runner for testing without real agents.

    Returns the ``task_description`` as its output so downstream nodes
    can see what each stub node was asked to do.
    """
    async def _stub(task_id: str, context: dict[str, Any]) -> str:
        return f"[stub:{task_id}] {task_description}"

    return _stub
