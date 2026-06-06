from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from memory.memory_manager import MemoryManager
    from agents.base_agent import ToolDefinition

__all__ = [
    "AgentRunner", "TaskStatus", "AgentTask",
    "AgentResult", "Plan", "SupervisorResult",
]

# Rust equivalent: type AgentRunner = Box<dyn Fn(AgentTask, HashMap<_, AgentResult>) -> Future>
AgentRunner = Callable[["AgentTask", dict[str, "AgentResult"]], Awaitable[str]]


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE    = "done"
    FAILED  = "failed"


@dataclass
class AgentTask:
    """One node in the workflow DAG. memory_manager and tools are injected by GoatSupervisor before execution."""

    id:             str
    role:           str
    prompt:         str
    depends_on:     list[str]            = field(default_factory=list)
    status:         TaskStatus           = field(default=TaskStatus.PENDING, compare=False)
    memory_manager: MemoryManager | None = field(default=None, repr=False)
    tools:          list[ToolDefinition] = field(default_factory=list, repr=False)
    source:         str                  = ""  # set by runner: net | memory | file | generated


@dataclass
class AgentResult:
    """Output of one AgentTask, including timing, model used, and optional error string."""

    task_id:         str
    role:            str
    output:          str
    model:           str
    duration_s:      float
    error:           str | None = None
    source:          str  = ""     # net | memory | file | generated
    tool_called:     bool = False  # True when ≥1 tool was invoked
    tool_name:       str  = ""     # primary tool called, inferred from source
    raw_output_hash: str  = ""     # SHA-256 16-char prefix of output

    @property
    def ok(self) -> bool:
        """True when the task completed without an error."""
        return self.error is None


@dataclass
class Plan:
    """Ordered list of AgentTask instances forming the execution DAG."""

    tasks: list[AgentTask]


@dataclass
class SupervisorResult:
    """Full output of a GoatSupervisor.run() call."""

    intent:           str
    plan:             Plan
    results:          dict[str, AgentResult]
    critique:         str
    summary:          str
    total_duration_s: float
    session_id:       str            = ""
    sources:          dict[str, str] = field(default_factory=dict)  # task_id -> SourceTag
    metadata_summary: str            = ""  # structured DAG execution metadata

    @property
    def success(self) -> bool:
        """True when all tasks completed without errors."""
        return all(r.ok for r in self.results.values())

    def to_dict(self) -> dict:
        """Serialize to a plain dict suitable for JSON output."""
        return {
            "intent":           self.intent,
            "summary":          self.summary,
            "critique":         self.critique,
            "success":          self.success,
            "total_duration_s": round(self.total_duration_s, 2),
            "session_id":       self.session_id,
            "sources":          self.sources,
            "metadata_summary": self.metadata_summary,
            "tasks": [
                {
                    "id": r.task_id, "role": r.role, "model": r.model,
                    "duration_s": round(r.duration_s, 2), "ok": r.ok,
                    "error": r.error, "source": r.source,
                    "tool_called": r.tool_called, "tool_name": r.tool_name,
                    "raw_output_hash": r.raw_output_hash,
                    "output": r.output[:500] + "…" if len(r.output) > 500 else r.output,
                }
                for r in self.results.values()
            ],
        }
