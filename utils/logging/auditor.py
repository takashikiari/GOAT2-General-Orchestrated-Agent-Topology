"""Audit log post-processor — run after a DAG completes to
flag anomalies in the per-task results and surface them via
``AuditReport.anomalies``.

Pure Python, no LLM, no I/O of its own. Reads the ``results``
dict that a DAG run produced, applies a small set of
rule-based checks, and returns an ``AuditReport`` the caller
can log or attach to a ``SupervisorResult``.

USAGE:
    from utils.logging.auditor import run_auditor, AuditReport

    audit = await run_auditor(results)   # results: dict[str, AgentResult]
    if audit.anomalies:
        log.warning("dag audit: %d anomaly(ies): %s",
                    len(audit.anomalies), audit.anomalies)

CHECKS APPLIED (all rule-based, all cheap):

  1. Every result that failed (``ok=False``) is recorded as
     an anomaly with the task id + error preview.
  2. Tasks that took longer than ``slow_threshold_s`` (default
     30s) are flagged as ``"slow"`` so the caller can spot
     runaway agents.
  3. Empty outputs from a successful task are flagged as
     ``"empty_output"`` so the caller can spot silent failures.
  4. Two tasks with identical output text are flagged as
     ``"duplicate_output:<hash>"`` so the caller can spot
     repeated boilerplate (a sign the agent is not actually
     doing fresh work).

The function is ``async`` (matching the original
``supervisor.logging.auditor.run_auditor`` signature) but the
body is synchronous — ``async def`` is preserved for
backward-compat with the existing call site.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Iterable, Mapping

if TYPE_CHECKING:
    from config.agent_types import AgentResult

log = logging.getLogger("goat2.utils.logging.auditor")

__all__ = ["AuditReport", "run_auditor", "DEFAULT_SLOW_THRESHOLD_S"]


# Slow-task threshold (seconds). Above this, a successful
# task is flagged. The constant is exposed so tests can
# override it; the value is also read at function call time
# to keep the audit deterministic per run.
DEFAULT_SLOW_THRESHOLD_S: Final[float] = 30.0

# Maximum chars of an error message included in an anomaly
# entry. Long tracebacks get truncated; the full text is
# still in the AgentResult.
_ERROR_PREVIEW_CHARS: Final[int] = 200

# How much of an empty-output string we log. Just a marker;
# the actual content (or lack thereof) is the issue.
_EMPTY_OUTPUT_MARKER: Final[str] = "<empty>"

# Hash prefix length for duplicate detection. Same logic as
# the structured logger — short is enough to correlate.
_DUP_HASH_PREFIX: Final[int] = 12


@dataclass
class AuditReport:
    """Result of ``run_auditor(results)``.

    Attributes:
        anomalies: Human-readable list of anomaly strings.
            One entry per detected issue. Empty list when
            everything looks healthy.
        checked: Number of results inspected.
        ok_count: Number of results with ``ok=True``.
        fail_count: Number of results with ``ok=False``.
    """

    anomalies: list[str] = field(default_factory=list)
    checked:   int          = 0
    ok_count:  int          = 0
    fail_count: int         = 0

    @property
    def has_anomalies(self) -> bool:
        """True when at least one anomaly was detected."""
        return bool(self.anomalies)


def _short_hash(text: str) -> str:
    """Return a 12-char SHA-256 prefix of ``text`` (or empty)."""
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:_DUP_HASH_PREFIX]


async def run_auditor(
    results: "Mapping[str, AgentResult] | Iterable[AgentResult]",
    *,
    slow_threshold_s: float = DEFAULT_SLOW_THRESHOLD_S,
) -> AuditReport:
    """Inspect a DAG's per-task results and flag anomalies.

    Args:
        results: Dict-like mapping of ``task_id → AgentResult``,
            or any iterable of ``AgentResult``. Dict order is
            preserved by ``Mapping``; list order by ``Iterable``.
        slow_threshold_s: Tasks that took longer than this
            (seconds) are flagged as ``"slow"``. Default 30s.

    Returns:
        An ``AuditReport`` whose ``anomalies`` list is empty
        when nothing was flagged. Never raises — best-effort.
    """
    report = AuditReport()
    # Normalize the input to a list of (id, result) tuples.
    # ``Mapping.items()`` is the primary path; iterables of
    # AgentResult get a synthetic index.
    pairs: list[tuple[str, "AgentResult"]]
    if isinstance(results, Mapping):
        pairs = list(results.items())
    else:
        pairs = [(f"task_{i}", r) for i, r in enumerate(results)]

    report.checked = len(pairs)
    # First pass: per-task anomalies (failures, slow, empty).
    output_hashes: dict[str, list[str]] = {}  # hash -> [task_id, ...]
    for task_id, r in pairs:
        # Defensive: a non-AgentResult shouldn't happen, but
        # we never want the auditor to raise.
        if r is None:
            report.anomalies.append(f"{task_id}: missing result")
            report.fail_count += 1
            continue
        ok = bool(getattr(r, "ok", False))
        if ok:
            report.ok_count += 1
        else:
            report.fail_count += 1
            err = (getattr(r, "error", "") or "").strip()
            if err:
                err = err[:_ERROR_PREVIEW_CHARS] + ("…" if len(err) > _ERROR_PREVIEW_CHARS else "")
                report.anomalies.append(f"{task_id}: failed — {err}")
            else:
                report.anomalies.append(f"{task_id}: failed (no error message)")

        # Slow-task check (only on successful tasks — a slow
        # failure is just a failure).
        if ok:
            duration = float(getattr(r, "duration_s", 0.0) or 0.0)
            if duration > slow_threshold_s:
                report.anomalies.append(
                    f"{task_id}: slow — {duration:.1f}s (threshold {slow_threshold_s:.0f}s)"
                )

        # Empty-output check (only on successful tasks).
        if ok:
            output = (getattr(r, "output", "") or "").strip()
            if not output:
                report.anomalies.append(f"{task_id}: empty_output")
            else:
                h = _short_hash(output)
                output_hashes.setdefault(h, []).append(task_id)

    # Second pass: duplicate-output check across the corpus.
    for h, ids in output_hashes.items():
        if len(ids) > 1 and h:
            joined = ", ".join(ids)
            report.anomalies.append(f"duplicate_output:{h} tasks=[{joined}]")

    log.debug(
        "run_auditor: checked=%d ok=%d fail=%d anomalies=%d",
        report.checked, report.ok_count, report.fail_count, len(report.anomalies),
    )
    return report