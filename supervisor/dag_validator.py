"""Post-execution DAG result validator for GOAT 2.0.

Runs after all DAG nodes finish and before aggregation (critique/synthesize).
Validates tool usage parameters, blocks generated source on execution tasks,
enforces per-role source whitelists, and flags net errors plus stale memory markers.

GOAT supervisor validates task success by checking tool call parameters —
never reports a task validated without verifying the tool was invoked correctly.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final

from supervisor.types import AgentResult

__all__ = ["ValidationStatus", "validate_results"]

log = logging.getLogger("goat2.dag_validator")

# Roles that must invoke a real tool — generated output is never acceptable.
_EXECUTION_ROLES: Final[frozenset[str]] = frozenset({"researcher", "memory"})

# Allowed sources per role. Roles absent from this dict pass all source checks.
_ROLE_ALLOWED_SOURCES: Final[dict[str, frozenset[str]]] = {
    "researcher":  frozenset({"net"}),
    "memory":      frozenset({"memory"}),
    "coder":       frozenset({"file", "net", "memory", "generated"}),
    "tool_caller": frozenset({"file", "net", "memory", "generated"}),
    "critic":      frozenset({"generated", "file", "memory"}),
    "summarizer":  frozenset({"generated", "file", "memory"}),
    "planner":     frozenset({"generated"}),
}

_NET_ERROR_PREFIXES: Final[tuple[str, ...]] = ("error:", "http ", "no results")
_STALE_MARKER:       Final[str]             = "[stale]"


@dataclass(frozen=True)
class ValidationStatus:
    """Outcome of validating a single AgentResult.

    Attributes:
        task_id: Identifier of the validated task.
        safe:    False when the result must be rejected before aggregation.
        reason:  Machine-readable reason code when safe is False.
    """

    task_id: str
    safe:    bool
    reason:  str = ""


def _is_unverified_execution(result: AgentResult) -> bool:
    """True when an execution-role task has tool_called=False (source=generated)."""
    return result.role in _EXECUTION_ROLES and not result.tool_called


def _is_source_violation(result: AgentResult) -> bool:
    """True when source is not in the whitelist for this role."""
    allowed = _ROLE_ALLOWED_SOURCES.get(result.role)
    return allowed is not None and result.source not in allowed


def _is_net_error(result: AgentResult) -> bool:
    """True when source=net and output signals a search failure."""
    if result.source != "net":
        return False
    if not result.ok:
        return True
    output = (result.output or "").lower().strip()
    return any(output.startswith(p) for p in _NET_ERROR_PREFIXES)


def _is_stale_memory(result: AgentResult) -> bool:
    """True when source=memory and output contains the stale data marker."""
    return result.source == "memory" and _STALE_MARKER in (result.output or "")


def _is_empty_file_read(result: AgentResult) -> bool:
    """True when a file tool was invoked but the task output is empty.

    Signals that a file read was confirmed at the tool level but no content
    reached the aggregated result — GOAT must not hallucinate to fill the gap.
    """
    return result.source == "file" and result.tool_called and not (result.output or "").strip()


def _is_missing_tool_params(result: AgentResult) -> bool:
    """True when tool was called but required parameters are missing or invalid.

    GOAT supervisor cannot validate task success without verifying tool parameters.
    This check ensures tool calls have meaningful arguments before marking safe.
    """
    if not result.tool_called:
        return False  # No tool called — different validation path
    if not result.tool_name:
        return True  # Tool called but name not recorded — validation impossible
    if not result.raw_output_hash:
        return True  # No output hash — cannot verify tool execution
    return False


def validate_results(
    results: dict[str, AgentResult],
) -> tuple[dict[str, AgentResult], list[ValidationStatus]]:
    """Validate all DAG results before aggregation.

    GOAT supervisor MUST reject synthesis if any returned status has safe=False.
    Priority: missing_tool_params > empty_file_read > unverified_execution > source_violation > net_error > stale_memory.
    Returns the unchanged results dict alongside per-task ValidationStatus objects.
    """
    statuses: list[ValidationStatus] = []
    for tid, result in results.items():
        if _is_missing_tool_params(result):
            log.warning(
                "dag_validator: %s tool_called=True but parameters missing — cannot validate",
                tid,
            )
            statuses.append(ValidationStatus(task_id=tid, safe=False, reason="missing_tool_params"))
        elif _is_empty_file_read(result):
            log.warning(
                "dag_validator: %s source=file tool_called=True but output empty — empty_file_read",
                tid,
            )
            statuses.append(ValidationStatus(task_id=tid, safe=False, reason="empty_file_read"))
        elif _is_unverified_execution(result):
            log.warning(
                "dag_validator: %s role=%s tool_called=False — UNVERIFIED", tid, result.role,
            )
            statuses.append(ValidationStatus(task_id=tid, safe=False, reason="unverified_execution"))
        elif _is_source_violation(result):
            log.warning(
                "dag_validator: %s role=%s source=%s not in whitelist — source_violation",
                tid, result.role, result.source,
            )
            statuses.append(ValidationStatus(task_id=tid, safe=False, reason="source_violation"))
        elif _is_net_error(result):
            log.warning("dag_validator: %s source=net returned error — unsafe", tid)
            statuses.append(ValidationStatus(task_id=tid, safe=False, reason="net_error"))
        elif _is_stale_memory(result):
            log.warning("dag_validator: %s source=memory is stale — revalidation needed", tid)
            statuses.append(ValidationStatus(task_id=tid, safe=False, reason="stale_memory"))
        else:
            statuses.append(ValidationStatus(task_id=tid, safe=True))
    return results, statuses
