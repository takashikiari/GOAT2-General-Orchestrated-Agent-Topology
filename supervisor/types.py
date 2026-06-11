"""Core types for GOAT 2.0 supervisor — backward-compatible re-exports + SupervisorResult.

AgentRunner, TaskStatus, AgentTask, AgentResult, Plan live in config.agent_types
so they can be imported by agents/ and tools/ without triggering the circular-import
chain through supervisor/__init__.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from config.agent_types import (
    AgentRunner,
    TaskStatus,
    AgentTask,
    AgentResult,
    Plan,
)

__all__ = [
    "AgentRunner", "TaskStatus", "AgentTask",
    "AgentResult", "Plan", "SupervisorResult",
]


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
    sources:          dict[str, str] = field(default_factory=dict)
    metadata_summary: str            = ""
    dag_verified:     bool           = False
    dag_detail:       str            = ""

    @property
    def success(self) -> bool:
        """True when all tasks completed without errors."""
        return all(r.ok for r in self.results.values())

    @property
    def validated(self) -> bool:
        """True when dag_result was retrieved from Redis (proves real DAG execution)."""
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
