"""Core types for GOAT 2.0 supervisor — tasks, results, and execution metadata.

All types are Rust-ready with explicit type hints and dataclasses.
AgentResult includes tool parameter tracking for validation.
"""
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
    """Task execution state in the workflow DAG."""
    PENDING = "pending"
    RUNNING = "running"
    DONE    = "done"
    FAILED  = "failed"


@dataclass
class AgentTask:
    """One node in the workflow DAG. memory_manager and tools are injected by GoatSupervisor before execution.

    DAG agents access tools but are restricted to working memory (Redis) with role="user_session".
    GOAT supervisor manages memory read/write directly across all three tiers.
    """

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
    """Output of one AgentTask, including timing, model used, and tool parameter validation.

    GOAT supervisor validates tool_called, tool_name, and raw_output_hash before
    marking a task as successful. Cannot report validated without parameter verification.
    """

    task_id:         str
    role:            str
    output:          str
    model:           str
    duration_s:      float
    error:           str | None = None
    source:          str  = ""     # net | memory | file | generated
    tool_called:     bool = False  # True when ≥1 tool was invoked with valid parameters
    tool_name:       str  = ""     # primary tool called, inferred from source
    raw_output_hash: str  = ""     # SHA-256 16-char prefix of output (validates execution)

    @property
    def ok(self) -> bool:
        """True when the task completed without an error."""
        return self.error is None

    @property
    def validated(self) -> bool:
        """True when task completed AND tool parameters can be verified.

        GOAT supervisor cannot report task validated without checking:
        - tool_called is True
        - tool_name is non-empty
        - raw_output_hash is non-empty (proves tool execution)
        """
        return self.ok and self.tool_called and bool(self.tool_name) and bool(self.raw_output_hash)


@dataclass
class Plan:
    """Ordered list of AgentTask instances forming the execution DAG."""

    tasks: list[AgentTask]


@dataclass
class SupervisorResult:
    """Full output of a GoatSupervisor.run() call.

    GOAT supervisor manages memory read/write directly. DAG agents access
    tools but are restricted to working memory (Redis) with role="user_session".
    """

    intent:           str
    plan:             Plan
    results:          dict[str, AgentResult]
    critique:         str
    summary:          str
    total_duration_s: float
    session_id:       str            = ""
    sources:          dict[str, str] = field(default_factory=dict)  # task_id -> SourceTag
    metadata_summary: str            = ""  # structured DAG execution metadata
    dag_verified:     bool           = False  # True when dag_result retrieved from Redis
    dag_detail:       str            = ""  # Full DAG execution detail for synthesis

    @property
    def success(self) -> bool:
        """True when all tasks completed without errors."""
        return all(r.ok for r in self.results.values())

    @property
    def validated(self) -> bool:
        """True when all tasks completed AND tool parameters verified AND dag_result retrieved.

        GOAT supervisor cannot report success without parameter validation.
        dag_verified must be True — ensures LLM synthesizes from real DAG output.
        """
        return self.dag_verified

    def to_dict(self) -> dict:
        """Serialize to a plain dict suitable for JSON output."""
        return {
            "intent":           self.intent,
            "summary":          self.summary,
            "critique":         self.critique,
            "success":          self.success,
            "validated":        self.validated,
            "total_duration_s": round(self.total_duration_s, 2),
            "session_id":       self.session_id,
            "sources":          self.sources,
            "metadata_summary": self.metadata_summary,
            "dag_verified":     self.dag_verified,
            "dag_detail":       self.dag_detail[:500] + "…" if len(self.dag_detail) > 500 else self.dag_detail,
            "tasks": [
                {
                    "id": r.task_id, "role": r.role, "model": r.model,
                    "duration_s": round(r.duration_s, 2), "ok": r.ok,
                    "validated": r.validated, "error": r.error, "source": r.source,
                    "tool_called": r.tool_called, "tool_name": r.tool_name,
                    "raw_output_hash": r.raw_output_hash,
                    "output": r.output[:500] + "…" if len(r.output) > 500 else r.output,
                }
                for r in self.results.values()
            ],
        }
