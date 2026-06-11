"""Shared agent types for GOAT 2.0.

Decoupled from supervisor/__init__.py so agents/ and tools/ can import these
types without triggering the circular-import chain:
    agents/base_agent.py → supervisor → registry → agents.planner_decompose
                        → supervisor/pipeline → tools → agents.base_agent
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from memory.shared import MemoryManager
    from agents.base_agent import ToolDefinition

__all__ = ["AgentRunner", "TaskStatus", "AgentTask", "AgentResult", "Plan"]

AgentRunner = Callable[["AgentTask", dict[str, "AgentResult"]], Awaitable[str]]


class TaskStatus(str, Enum):
    """Task execution state in the workflow DAG."""

    PENDING = "pending"
    RUNNING = "running"
    DONE    = "done"
    FAILED  = "failed"


@dataclass
class AgentTask:
    """One node in the workflow DAG.

    memory_manager and tools are injected by GoatSupervisor before execution.
    DAG agents access tools but are restricted to working memory (Redis).
    """

    id:             str
    role:           str
    prompt:         str
    depends_on:     list[str]            = field(default_factory=list)
    status:         TaskStatus           = field(default=TaskStatus.PENDING, compare=False)
    memory_manager: MemoryManager | None = field(default=None, repr=False)
    tools:          list[ToolDefinition] = field(default_factory=list, repr=False)
    source:         str                  = ""


@dataclass
class AgentResult:
    """Output of one AgentTask, including timing, model used, and tool validation."""

    task_id:         str
    role:            str
    output:          str
    model:           str
    duration_s:      float
    error:           str | None = None
    source:          str  = ""
    tool_called:     bool = False
    tool_name:       str  = ""
    raw_output_hash: str  = ""

    @property
    def ok(self) -> bool:
        """True when the task completed without an error."""
        return self.error is None

    @property
    def validated(self) -> bool:
        """True when task completed AND tool parameters can be verified."""
        return self.ok and self.tool_called and bool(self.tool_name) and bool(self.raw_output_hash)


@dataclass
class Plan:
    """Ordered list of AgentTask instances forming the execution DAG."""

    tasks: list[AgentTask]
