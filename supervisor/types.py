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
    """Output of one ``GoatSupervisor.run()`` call.

    Attributes:
        intent:      The user's original intent.
        summary:     The user-facing reply text.
        session_id:  The supervisor's session id.
        sources:     Provenance map (``{"conv": source}``).
        duration_s:  Wall-clock seconds the run took.
        action:      ``direct`` | ``clarify`` | ``dag`` (from the turn).
    """

    intent:      str
    summary:     str
    session_id:  str            = ""
    sources:     dict[str, str] = field(default_factory=dict)
    duration_s:  float          = 0.0
    action:      str            = "direct"