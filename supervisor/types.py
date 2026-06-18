"""Core types for the GOAT 2.0 supervisor — re-exports from
``config.agent_types`` plus the ``SupervisorResult`` dataclass
the supervisor returns from ``run()``.

Lives in the supervisor package (not config) because
``SupervisorResult`` is a supervisor-specific output; the
underlying ``Plan`` / ``TaskStatus`` / ``AgentResult`` types
are shared with the rest of the codebase and are imported
here for convenience.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from config.agent_types import (
    AgentResult,
    AgentTask,
    AgentRunner,
    Plan,
    TaskStatus,
)

__all__ = [
    "AgentRunner",
    "AgentTask",
    "AgentResult",
    "Plan",
    "SupervisorResult",
    "TaskStatus",
]


@dataclass
class SupervisorResult:
    """Full output of a ``GoatSupervisor.run()`` call.

    Attributes:
        intent:           The user's original intent.
        plan:             Plan produced for the turn (empty in
            the single-call architecture — the LLM may have
            spawned a DAG via the ``start_dag`` tool, in which
            case the result of that DAG arrives on a later turn).
        results:          Per-task results (usually empty in the
            single-call path; populated by callers that wire in
            a workflow layer).
        critique:         Empty in the single-call path.
        summary:          The user-facing reply text.
        total_duration_s: Wall-clock time the run took.
        session_id:       The supervisor's session id.
        sources:          Provenance map (``{"conv": source}``).
        metadata_summary: Optional structured summary.
        dag_verified:     True when a DAG result was retrieved
            from Redis (proves real execution).
        dag_detail:       The DAG result text (truncated at 500
            chars in ``to_dict``).
    """

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
        """True when a DAG result was retrieved (proves execution)."""
        return self.dag_verified

    def to_dict(self) -> dict:
        """Serialize to a JSON-safe dict."""
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
            "dag_detail": (
                self.dag_detail[:500] + "…"
                if len(self.dag_detail) > 500
                else self.dag_detail
            ),
            "tasks": [
                {
                    "id": r.task_id, "role": r.role, "model": r.model,
                    "duration_s": round(r.duration_s, 2), "ok": r.ok,
                    "validated": r.validated, "error": r.error,
                    "source": r.source, "tool_called": r.tool_called,
                    "tool_name": r.tool_name,
                    "raw_output_hash": r.raw_output_hash,
                    "output": (
                        r.output[:500] + "…"
                        if len(r.output) > 500
                        else r.output
                    ),
                }
                for r in self.results.values()
            ],
        }