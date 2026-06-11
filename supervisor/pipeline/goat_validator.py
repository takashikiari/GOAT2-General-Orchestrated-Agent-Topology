"""GOAT Validator — Post-DAG validation before synthesis.

Validates DAG results before synthesis to ensure role conformity,
source tags, tool calls present, and no hallucination markers.
If validation passes → synthesize. If validation fails → _unverified_summary.

TOOL DISTRIBUTION:
=================
- DAG agents: FILE_TOOLS + WEB_SEARCH + DAG_MEMORY_TOOLS (dag:* namespace)
- GOAT CONVERSATIONAL: FILE_TOOLS + MEMORY_TOOLS (all tiers, goat:* namespace)
- GOAT VALIDATOR: direct memory_manager access only, no tool calls
- GOAT Memory Promoter: direct memory_manager.promote() only

ARCHITECTURE NOTE:
================
GOAT Validator is a pipeline component like the critic.
It runs after dag_result is retrieved from Redis and before
synthesis to ensure the LLM synthesizes from real output,
not hallucinated content.

This is distinct from dag_validator.py because:
- dag_validator.py: Validates AgentResult fields (source, tool_called, etc.)
- goat_validator.py: Validates the full DAG result structure and content
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Final

from config.agents import AGENT_ROLES, EXECUTION_ROLES

log = logging.getLogger("goat2.supervisor.pipeline")

__all__ = ["ValidationReport", "validate_dag_result", "validate_dag_result_simple"]

# Valid source tags
VALID_SOURCES: Final[frozenset[str]] = frozenset({"net", "file", "memory", "generated"})

# Hallucination markers to detect
HALLUCINATION_MARKERS: Final[list[str]] = [
    "i cannot",
    "i'm unable",
    "i'm not able",
    "as an ai",
    "as a language model",
    "i don't have the ability",
    "i do not have access",
    "i cannot provide",
    "please note that i am an ai",
    "note: this is a placeholder",
    "[placeholder]",
    "[todo]",
    "tbd",
    "to be determined",
    "coming soon",
    "not yet implemented",
    "error: not found",
    "error: no results",
]


@dataclass(frozen=True)
class ValidationReport:
    """Outcome of validating DAG result before synthesis.

    Attributes:
        passed: True if all validation checks passed.
        role_conformity: True if all tasks have valid roles.
        source_tags_valid: True if all source tags are valid.
        tool_calls_present: True if execution roles have tool calls.
        no_hallucination_markers: True if no hallucination markers detected.
        errors: List of validation error messages.
    """

    passed: bool = False
    role_conformity: bool = True
    source_tags_valid: bool = True
    tool_calls_present: bool = True
    no_hallucination_markers: bool = True
    errors: list[str] = field(default_factory=list)


def _parse_dag_result(dag_detail: str) -> dict:
    """Parse DAG result JSON from Redis storage.

    Args:
        dag_detail: The DAG result string from Redis.

    Returns:
        Parsed dictionary with session_id, completed_at, tasks.
    """
    import json

    try:
        return json.loads(dag_detail)
    except json.JSONDecodeError as e:
        log.warning("goat_validator: failed to parse dag_detail: %s", e)
        return {}


def _validate_role(role: str) -> bool:
    """Check if role is valid.

    Args:
        role: The role string to validate.

    Returns:
        True if role is in AGENT_ROLES.
    """
    return role in AGENT_ROLES


def _validate_source(source: str) -> bool:
    """Check if source tag is valid.

    Args:
        source: The source tag to validate.

    Returns:
        True if source is in VALID_SOURCES.
    """
    return source in VALID_SOURCES


def _has_hallucination_markers(text: str) -> bool:
    """Check for hallucination markers in text.

    Args:
        text: Text to check for markers.

    Returns:
        True if any hallucination marker found.
    """
    text_lower = text.lower()
    for marker in HALLUCINATION_MARKERS:
        if marker in text_lower:
            return True
    return False


def _extract_task_outputs(dag_detail: str) -> dict[str, str]:
    """Extract task outputs from DAG result.

    Args:
        dag_detail: The DAG result string from Redis.

    Returns:
        Dictionary mapping task_id to output text.
    """
    parsed = _parse_dag_result(dag_detail)
    tasks = parsed.get("tasks", {})
    return {
        tid: info.get("output", "")
        for tid, info in tasks.items()
    }


def validate_dag_result(
    dag_detail: str,
    results: dict,
) -> ValidationReport:
    """Validate DAG result before synthesis.

    Checks:
    - Role conformity: Each task has valid role from AGENT_ROLES
    - Source tags: Source is one of {net, file, memory, generated}
    - Tool calls present: Execution roles must have tool_called=True
    - No hallucination markers: Check for common hallucination phrases

    Args:
        dag_detail: The DAG result string from Redis (from retrieve_dag_result).
        results: Dictionary of task_id -> AgentResult from workflow execution.

    Returns:
        ValidationReport with pass/fail status and error details.
    """
    errors: list[str] = []

    # Parse dag_detail to get task information
    task_outputs = _extract_task_outputs(dag_detail)

    # 1. Check role conformity
    role_errors = []
    for tid, result in results.items():
        if not _validate_role(result.role):
            role_errors.append(f"task={tid} role={result.role}")
    if role_errors:
        errors.append(f"role_conformity: {', '.join(role_errors)}")

    # 2. Check source tags
    source_errors = []
    for tid, result in results.items():
        if not _validate_source(result.source):
            source_errors.append(f"task={tid} source={result.source}")
    if source_errors:
        errors.append(f"source_tags: {', '.join(source_errors)}")

    # 3. Check tool calls for execution roles
    tool_errors = []
    for tid, result in results.items():
        if result.role in EXECUTION_ROLES and not result.tool_called:
            tool_errors.append(f"task={tid} role={result.role}")
    if tool_errors:
        errors.append(f"tool_calls_missing: {', '.join(tool_errors)}")

    # 4. Check for hallucination markers in outputs
    hallucination_errors = []
    for tid, output in task_outputs.items():
        if _has_hallucination_markers(output):
            # Truncate for logging
            preview = output[:100] + "..." if len(output) > 100 else output
            hallucination_errors.append(f"task={tid} output={preview}")
    if hallucination_errors:
        errors.append(f"hallucination_markers: {', '.join(hallucination_errors[:3])}")

    # Build report
    passed = len(errors) == 0
    report = ValidationReport(
        passed=passed,
        role_conformity=len(role_errors) == 0,
        source_tags_valid=len(source_errors) == 0,
        tool_calls_present=len(tool_errors) == 0,
        no_hallucination_markers=len(hallucination_errors) == 0,
        errors=errors,
    )

    if passed:
        log.info("goat_validator: validation passed")
    else:
        log.warning("goat_validator: validation failed - %s", errors)

    return report


def validate_dag_result_simple(dag_detail: str) -> bool:
    """Simple validation that just checks if dag_detail is valid JSON.

    This is a lightweight check for cases where full validation
    is not needed. Returns True if:
    - dag_detail is non-empty
    - Contains valid JSON with 'tasks' key
    - At least one task has non-empty output

    Args:
        dag_detail: The DAG result string from Redis.

    Returns:
        True if validation passes, False otherwise.
    """
    if not dag_detail or not dag_detail.strip():
        log.warning("goat_validator: dag_detail is empty")
        return False

    parsed = _parse_dag_result(dag_detail)

    # Must have tasks
    tasks = parsed.get("tasks", {})
    if not tasks:
        log.warning("goat_validator: no tasks in dag_detail")
        return False

    # At least one task should have output
    has_output = any(
        info.get("output", "").strip()
        for info in tasks.values()
    )
    if not has_output:
        log.warning("goat_validator: no task outputs found")
        return False

    return True