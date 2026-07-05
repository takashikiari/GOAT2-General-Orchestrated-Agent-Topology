"""workflow.agent_node — bridges agents/ templates to DAG NodeRunner callables.

A ``NodeRunner`` has signature ``async (task_id, context) -> Any``.
``make_runner`` wraps a ``BaseAgent.execute()`` call in that signature,
translating the DAG context dict into ``AgentTask`` / ``AgentResult``
objects that agents understand.

Agent availability is checked at runner-creation time, not at module import,
so the workflow engine stays importable even when ``config.agent_types`` does
not yet exist.
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
    1. Retrieves the agent from ``router`` (cached instantiation).
    2. Converts the DAG context into ``{dep_id: AgentResult}`` objects.
    3. Constructs an ``AgentTask`` with ``task_description`` as prompt.
    4. Calls ``agent.execute(task, dep_context)`` and returns the string result.

    Args:
        role: Agent role name (e.g. ``"planner"``).
        task_description: Natural-language task prompt given to the agent.
        router: ``AgentRouter`` instance used to resolve the agent class.

    Returns:
        An async callable compatible with ``TaskNode.runner``.

    Raises:
        ImportError: At call time if ``config.agent_types`` is not importable.
    """
    # Validate role is known before returning runner
    if role not in AgentRouter.registered_roles():
        raise ValueError(f"Unknown agent role: {role!r}")

    async def _runner(task_id: str, context: dict[str, Any]) -> str:
        # Lazy import — only fails at call time, not at module import
        try:
            from config.agent_types import AgentResult, AgentTask  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "config.agent_types is required to run agent nodes. "
                f"Original error: {exc}"
            ) from exc

        agent = router.get(role)

        # Convert DAG context values to AgentResult objects for deps that have
        # run before this node.  Plain strings pass through; other types are
        # wrapped in a minimal AgentResult.
        dep_context: dict[str, Any] = {}
        for dep_id, dep_value in context.items():
            if dep_id.startswith("__"):
                continue  # skip internal keys like __working_dir__
            if hasattr(dep_value, "output"):
                dep_context[dep_id] = dep_value  # already an AgentResult
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
