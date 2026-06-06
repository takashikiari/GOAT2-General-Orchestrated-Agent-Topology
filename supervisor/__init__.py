"""
supervisor — GOAT 2.0 workflow orchestration package.

Backward-compatible re-exports: code that did
    from supervisor import GoatSupervisor, AgentTask, AgentResult
continues to work unchanged.
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
    registry: AgentRegistry | None = None,
    memory_manager=None,
) -> SupervisorResult:
    """Top-level convenience entry point: asyncio.run(run('…'))."""
    return await GoatSupervisor(registry, memory_manager).run(intent)

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
