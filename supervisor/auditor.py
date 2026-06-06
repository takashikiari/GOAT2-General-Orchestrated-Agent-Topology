"""Auditor agent for GOAT 2.0 — cross-tool consistency check after DAG execution.

Compares AgentResult outputs from tasks that share the same role. Reports
an anomaly when the word-level Jaccard similarity between two results falls
below the threshold, indicating significant divergence in their conclusions.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Final

from supervisor.types import AgentResult

__all__ = ["AuditReport", "run_auditor"]

log = logging.getLogger("goat2.auditor")

_SIMILARITY_THRESHOLD: Final[float] = 0.30
_MIN_CONTENT_LEN:      Final[int]   = 20


@dataclass
class AuditReport:
    """Summary of the auditor's cross-tool consistency pass.

    Attributes:
        anomalies: Human-readable descriptions of detected discrepancies.
        compared_pairs: Total number of result pairs evaluated.
    """

    anomalies: list[str]     = field(default_factory=list)
    compared_pairs: int      = 0

    @property
    def clean(self) -> bool:
        """True when no anomalies were detected in this execution."""
        return len(self.anomalies) == 0


def _jaccard(a: str, b: str) -> float:
    """Compute word-level Jaccard similarity between two text strings."""
    sa = set(a.lower().split())
    sb = set(b.lower().split())
    if not sa and not sb:
        return 1.0
    return len(sa & sb) / len(sa | sb)


async def run_auditor(results: dict[str, AgentResult]) -> AuditReport:
    """Compare AgentResults sharing the same role; flag significant divergence.

    Two results diverge when their Jaccard word-overlap is below
    _SIMILARITY_THRESHOLD. Only compares results that are non-error and
    have at least _MIN_CONTENT_LEN characters of output.
    """
    report = AuditReport()
    by_role: dict[str, list[AgentResult]] = {}
    for r in results.values():
        if r.ok and len(r.output or "") >= _MIN_CONTENT_LEN:
            by_role.setdefault(r.role, []).append(r)

    for role, role_results in by_role.items():
        if len(role_results) < 2:
            continue
        for i, ra in enumerate(role_results):
            for rb in role_results[i + 1:]:
                report.compared_pairs += 1
                sim = _jaccard(ra.output, rb.output)
                if sim < _SIMILARITY_THRESHOLD:
                    msg = (
                        f"Anomaly: role={role} "
                        f"tasks=({ra.task_id}, {rb.task_id}) "
                        f"jaccard={sim:.2f} < {_SIMILARITY_THRESHOLD}"
                    )
                    log.warning(msg)
                    report.anomalies.append(msg)

    return report
