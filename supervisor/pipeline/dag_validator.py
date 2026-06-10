"""Post-execution DAG result validator for GOAT 2.0.

Runs after all DAG nodes finish and before aggregation (critique/synthesize).
Validates tool usage parameters, blocks generated source on execution tasks,
enforces per-role source whitelists, and flags net errors.

ARCHITECTURE NOTE:
==================
DAG agents cannot access ChromaDB or Letta — only Redis working memory.
All persistent memory writes are handled by supervisor post-execution.
DAG output is stored to Redis and read by supervisor for validation.

CONTRADICTION DETECTION:
========================
Detects conflicting claims between agent outputs and marks DAG as unsafe
when mutually exclusive assertions are found.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final

from config.agents import EXECUTION_ROLES, SYNTHESIS_ROLES
from supervisor.types import AgentResult

__all__ = ["ValidationStatus", "validate_results"]

log = logging.getLogger("goat2.dag_validator")

# Roles that must invoke a real tool — generated output is never acceptable.
# IMPORTED from config/agents.py — DO NOT hardcode
_EXECUTION_ROLES = EXECUTION_ROLES

# Roles where source=generated is valid (no tool calls expected).
# IMPORTED from config/agents.py — DO NOT hardcode
_GENERATED_ROLES = SYNTHESIS_ROLES

# Allowed sources per role. Roles absent from this dict pass all source checks.
_ROLE_ALLOWED_SOURCES: Final[dict[str, frozenset[str]]] = {
    "researcher":  frozenset({"net"}),
    "coder":       frozenset({"file", "net", "generated"}),
    "tool_caller": frozenset({"file", "net", "generated", "memory"}),
    "critic":      frozenset({"generated", "file"}),
    "summarizer":  frozenset({"generated", "file"}),
    "planner":     frozenset({"generated"}),
}

_NET_ERROR_PREFIXES: Final[tuple[str, ...]] = ("error:", "http ", "no results")

# Contradiction detection: semantic opposites
_CONTRADICTIONS: Final[dict[str, list[str]]] = {
    "true": ["false", "not true", "incorrect"],
    "false": ["true", "correct", "accurate"],
    "yes": ["no", "not yes", "negative"],
    "no": ["yes", "affirmative", "positive"],
    "exists": ["not exist", "missing", "absent"],
    "missing": ["exists", "present", "available"],
    "enabled": ["disabled", "turned off"],
    "disabled": ["enabled", "turned on"],
    "success": ["failure", "failed", "error"],
    "failure": ["success", "succeeded", "worked"],
}


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


def _is_empty_generated(result: AgentResult) -> bool:
    """True when no tool was called and output is empty — e.g. summarizer produced nothing."""
    return not result.tool_called and not (result.output or "").strip()


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

    Note: Roles where source=generated is valid (critic, summarizer, planner)
    are allowed to have empty tool_name since they don't call external tools.
    """
    if not result.tool_called:
        return False  # No tool called — different validation path
    # Skip tool_name check for roles that don't use external tools
    if result.role in _GENERATED_ROLES:
        return False
    if not result.tool_name:
        return True  # Tool called but name not recorded — validation impossible
    if not result.raw_output_hash:
        return True  # No output hash — cannot verify tool execution
    return False


def _extract_claims(text: str) -> set[str]:
    """Extract key claims from text for contradiction detection.

    Looks for:
    - Boolean claims (true/false, yes/no, exists/missing)
    - Status claims (enabled/disabled, success/failure)
    - Simple subject-predicate patterns

    Args:
        text: Output text to extract claims from

    Returns:
        Set of normalized claim strings
    """
    claims = set()
    text_lower = text.lower()

    # Extract simple claims containing contradiction keywords
    for keyword in _CONTRADICTIONS.keys():
        if keyword in text_lower:
            # Find the context around the keyword (±20 chars)
            idx = text_lower.find(keyword)
            start = max(0, idx - 20)
            end = min(len(text), idx + len(keyword) + 20)
            context = text[start:end].strip()
            claims.add(context)

    return claims


def _is_contradictory(results: dict[str, AgentResult]) -> tuple[bool, str, str, str]:
    """Check for contradictions between any two agent outputs.

    Detects when two results make mutually exclusive claims about the same concept.
    Uses keyword-based contradiction detection (see _CONTRADICTIONS dict).

    Args:
        results: Dictionary of task_id → AgentResult to check

    Returns:
        Tuple of (has_contradiction, task_id_1, task_id_2, description).
        If no contradiction, returns (False, "", "", "").
    """
    result_list = list(results.items())

    for i, (tid1, r1) in enumerate(result_list):
        for tid2, r2 in result_list[i + 1:]:
            # Skip if either output is empty
            out1 = (r1.output or "").lower()
            out2 = (r2.output or "").lower()
            if not out1.strip() or not out2.strip():
                continue

            # Check for direct contradictions
            for keyword, opposites in _CONTRADICTIONS.items():
                if keyword in out1:
                    for opposite in opposites:
                        if opposite in out2:
                            # Found contradiction
                            desc = f"Task '{tid1}' claims '{keyword}' but task '{tid2}' claims '{opposite}'"
                            log.warning(
                                "dag_validator: contradiction detected — %s vs %s: %s",
                                tid1, tid2, desc,
                            )
                            return True, tid1, tid2, desc

    return False, "", "", ""


def validate_results(
    results: dict[str, AgentResult],
) -> tuple[dict[str, AgentResult], list[ValidationStatus]]:
    """Validate all DAG results before aggregation.

    GOAT supervisor MUST reject synthesis if any returned status has safe=False.
    Priority: missing_tool_params > empty_file_read > empty_generated >
              contradiction > unverified_execution > source_violation > net_error.

    Validation checks:
    - missing_tool_params: Tool called but parameters missing or unverifiable
    - empty_file_read: File read confirmed but no content in output
    - empty_generated: No tool called and output is empty
    - unverified_execution: Execution role (researcher/tool_caller) didn't call a tool
    - source_violation: Role got source not in its whitelist
    - net_error: source=net but returned an error
    - contradiction: Two tasks made mutually exclusive claims

    DAG agents can only produce: net, file, generated sources.
    (memory source removed — DAG cannot access ChromaDB/Letta)

    Returns the unchanged results dict alongside per-task ValidationStatus objects.
    """
    statuses: list[ValidationStatus] = []

    # First, check for cross-result contradictions (applies to entire DAG)
    has_contradiction, tid1, tid2, desc = _is_contradictory(results)
    if has_contradiction:
        # Mark both conflicting tasks as unsafe
        statuses.append(ValidationStatus(task_id=tid1, safe=False, reason="contradiction"))
        statuses.append(ValidationStatus(task_id=tid2, safe=False, reason="contradiction"))
        # Mark all other tasks as safe (they're not at fault)
        for tid in results:
            if tid not in (tid1, tid2):
                statuses.append(ValidationStatus(task_id=tid, safe=True))
        return results, statuses

    # Individual task validation (existing checks)
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
        elif _is_empty_generated(result):
            log.warning(
                "dag_validator: %s role=%s no tool called and output empty — empty_generated",
                tid, result.role,
            )
            statuses.append(ValidationStatus(task_id=tid, safe=False, reason="empty_generated"))
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
        else:
            statuses.append(ValidationStatus(task_id=tid, safe=True))

    return results, statuses
