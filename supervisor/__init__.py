"""
supervisor — GOAT 2.0 workflow orchestration package.

PHASE 4 UPDATE: run() now requires ServiceRegistry parameter.
Legacy singleton fallback removed.
"""

from supervisor.types import (
    AgentRunner,
    TaskStatus,
    AgentTask,
    AgentResult,
    Plan,
    SupervisorResult,
)
from supervisor.registry import AgentRegistry
from supervisor.workflow import WorkflowGraph
from supervisor.supervisor import GoatSupervisor


async def run(
    intent: str,
    registry,
) -> SupervisorResult:
    """Top-level convenience entry point: asyncio.run(run('…')).

    PHASE 4: ServiceRegistry parameter is now REQUIRED.
    Legacy singleton fallback removed.

    Args:
        intent: User intent string
        registry: ServiceRegistry instance for dependency injection

    Example:
        from config.registry import ServiceRegistry
        from supervisor import run
        
        registry = ServiceRegistry()
        result = await run("Build a REST API", registry=registry)
    """
    return await GoatSupervisor(registry).run(intent)

__all__ = [
    "GoatSupervisor",
    "AgentRegistry",
    "WorkflowGraph",
    "AgentRunner",
    "TaskStatus",
    "AgentTask",
    "AgentResult",
    "Plan",
    "SupervisorResult",
    "run",
]
