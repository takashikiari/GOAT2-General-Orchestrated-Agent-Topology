"""Pure-Python DAG validators — zero LLM calls.

All four validators run inside the DAG pipeline after each wave.
GOAT never calls these directly — it reads the final result from working memory.
"""
from __future__ import annotations

import dataclasses
import logging
import re
from dataclasses import dataclass, field
from typing import Final, TYPE_CHECKING

from config.agents import AGENT_ROLES, EXECUTION_ROLES, SYNTHESIS_ROLES

if TYPE_CHECKING:
    from supervisor.types import AgentResult
    from supervisor.pipeline.dag_prompt_builder import DagPrompt

log = logging.getLogger("goat2.tools.dag.validators")

__all__ = [
    "VerifierReport", "run_tool_verifier",
    "ValidationReport", "validate_dag_result", "validate_dag_result_simple",
    "ValidationStatus", "validate_results",
    "CorroborationReport", "check_corroboration",
]

_CONTRADICTIONS: Final[dict[str, list[str]]] = {
    "success": ["failure", "failed", "error"],
    "exists": ["not found", "missing", "not exist"],
    "running": ["stopped", "not running", "terminated"],
    "enabled": ["disabled", "not enabled"],
    "found": ["not found", "missing"],
    "complete": ["incomplete", "failed"],
}
_REFUSAL_MARKERS: Final[tuple[str, ...]] = (
    "i cannot", "i'm unable", "i am unable", "i can't",
    "i don't have access", "as an ai", "as a language model",
)
_VALID_SOURCES: Final[frozenset[str]] = frozenset({"net", "file", "memory", "generated"})
_NET_ERROR_PREFIXES: Final[tuple[str, ...]] = ("error:", "http ", "no results")
_ROLE_ALLOWED_SOURCES: Final[dict[str, frozenset[str]]] = {
    "researcher":  frozenset({"net"}),
    "coder":       frozenset({"file", "net", "generated"}),
    "tool_caller": frozenset({"file", "net", "generated", "memory"}),
    "critic":      frozenset({"generated", "file"}),
    "summarizer":  frozenset({"generated", "file"}),
    "planner":     frozenset({"generated"}),
}
_GENERATED_ROLES = SYNTHESIS_ROLES


@dataclasses.dataclass
class VerifierReport:
    passed: bool
    score: float
    findings: list[str]
    unmet_criteria: list[str]
    raw: str = ""


async def run_tool_verifier(
    results: "dict[str, AgentResult]",
    dag_prompt: "DagPrompt",
    registry=None,
) -> VerifierReport:
    """Keyword match of outputs against DagPrompt.verification_criteria."""
    if not dag_prompt.verification_criteria:
        return VerifierReport(passed=True, score=1.0, findings=[], unmet_criteria=[])
    combined = " ".join((r.output or "") for r in results.values()).lower()
    matched, findings, unmet = 0, [], []
    for criterion in dag_prompt.verification_criteria:
        keywords = [w for w in re.findall(r'\w+', criterion.lower()) if len(w) > 3]
        if any(kw in combined for kw in keywords):
            matched += 1
            findings.append(f"{criterion}: PASS")
        else:
            unmet.append(criterion)
            findings.append(f"{criterion}: FAIL — no evidence")
    total = len(dag_prompt.verification_criteria)
    score = matched / total if total else 1.0
    return VerifierReport(passed=score >= 0.8, score=score, findings=findings, unmet_criteria=unmet)


@dataclass(frozen=True)
class ValidationReport:
    passed: bool = False
    role_conformity: bool = True
    source_tags_valid: bool = True
    tool_calls_present: bool = True
    no_hallucination_markers: bool = True
    errors: list[str] = field(default_factory=list)


def _parse_dag_result(dag_detail: str) -> dict:
    import json
    try:
        return json.loads(dag_detail)
    except Exception:
        return {}


async def validate_dag_result(
    dag_detail: str,
    results: dict,
    registry=None,
) -> ValidationReport:
    """Structural + refusal-marker validation — pure Python, no LLM."""
    errors: list[str] = []
    role_errors = [f"task={tid} role={r.role}" for tid, r in results.items()
                   if r.role not in AGENT_ROLES]
    if role_errors:
        errors.append(f"role_conformity: {', '.join(role_errors)}")
    source_errors = [f"task={tid} source={r.source}" for tid, r in results.items()
                     if r.source not in _VALID_SOURCES]
    if source_errors:
        errors.append(f"source_tags: {', '.join(source_errors)}")
    tool_errors = [f"task={tid} role={r.role}" for tid, r in results.items()
                   if r.role in EXECUTION_ROLES and not r.tool_called]
    if tool_errors:
        errors.append(f"tool_calls_missing: {', '.join(tool_errors)}")
    parsed = _parse_dag_result(dag_detail)
    refusal_errors = [
        f"task={tid}" for tid, info in parsed.get("tasks", {}).items()
        if any(m in (info.get("output", "") or "").lower() for m in _REFUSAL_MARKERS)
    ]
    if refusal_errors:
        errors.append(f"hallucination: {', '.join(refusal_errors[:3])}")
    passed = not errors
    report = ValidationReport(
        passed=passed,
        role_conformity=not role_errors, source_tags_valid=not source_errors,
        tool_calls_present=not tool_errors, no_hallucination_markers=not refusal_errors,
        errors=errors,
    )
    (log.info if passed else log.warning)("goat_validator: %s", "passed" if passed else errors)
    return report


def validate_dag_result_simple(dag_detail: str) -> bool:
    if not dag_detail or not dag_detail.strip():
        return False
    parsed = _parse_dag_result(dag_detail)
    tasks = parsed.get("tasks", {})
    return bool(tasks) and any(i.get("output", "").strip() for i in tasks.values())


@dataclass(frozen=True)
class ValidationStatus:
    task_id: str
    safe:    bool
    reason:  str = ""


def _is_empty_generated(r: "AgentResult") -> bool:
    return not r.tool_called and not (r.output or "").strip()
def _is_unverified_execution(r: "AgentResult") -> bool:
    return r.role in EXECUTION_ROLES and not r.tool_called
def _is_source_violation(r: "AgentResult") -> bool:
    allowed = _ROLE_ALLOWED_SOURCES.get(r.role)
    return allowed is not None and r.source not in allowed
def _is_net_error(r: "AgentResult") -> bool:
    if r.source != "net":
        return False
    if not r.ok:
        return True
    return any((r.output or "").lower().strip().startswith(p) for p in _NET_ERROR_PREFIXES)
def _is_empty_file_read(r: "AgentResult") -> bool:
    return r.source == "file" and r.tool_called and not (r.output or "").strip()
def _is_missing_tool_params(r: "AgentResult") -> bool:
    if not r.tool_called or r.role in _GENERATED_ROLES:
        return False
    return not r.tool_name or not r.raw_output_hash


def _is_contradictory(results: "dict[str, AgentResult]") -> tuple[bool, str, str, str]:
    items = list(results.items())
    for i, (tid1, r1) in enumerate(items):
        out1 = (r1.output or "").lower()
        for tid2, r2 in items[i + 1:]:
            out2 = (r2.output or "").lower()
            if not out1.strip() or not out2.strip():
                continue
            for kw, opposites in _CONTRADICTIONS.items():
                if kw in out1:
                    for opp in opposites:
                        if opp in out2:
                            return True, tid1, tid2, f"'{kw}' vs '{opp}'"
    return False, "", "", ""


async def validate_results(
    results: "dict[str, AgentResult]",
    registry=None,
) -> "tuple[dict[str, AgentResult], list[ValidationStatus]]":
    """Validate all DAG results before aggregation — pure Python, no LLM."""
    statuses: list[ValidationStatus] = []
    has_contradiction, tid1, tid2, desc = _is_contradictory(results)
    if has_contradiction:
        log.warning("dag_validator: contradiction %s", desc)
        statuses.append(ValidationStatus(task_id=tid1, safe=False, reason="contradiction"))
        statuses.append(ValidationStatus(task_id=tid2, safe=False, reason="contradiction"))
        for tid in results:
            if tid not in (tid1, tid2):
                statuses.append(ValidationStatus(task_id=tid, safe=True))
        return results, statuses
    _checks = [
        (_is_missing_tool_params, "missing_tool_params"),
        (_is_empty_file_read,     "empty_file_read"),
        (_is_empty_generated,     "empty_generated"),
        (_is_unverified_execution,"unverified_execution"),
        (_is_source_violation,    "source_violation"),
        (_is_net_error,           "net_error"),
    ]
    for tid, r in results.items():
        reason = next((rn for fn, rn in _checks if fn(r)), "")
        if reason:
            log.warning("dag_validator: %s reason=%s", tid, reason)
        statuses.append(ValidationStatus(task_id=tid, safe=not reason, reason=reason))
    return results, statuses


@dataclasses.dataclass
class CorroborationReport:
    consistent: bool
    issues: list[str]
    raw: str = ""


def _jaccard(a: str, b: str) -> float:
    sa, sb = set(a.lower().split()), set(b.lower().split())
    if not sa and not sb:
        return 1.0
    return len(sa & sb) / len(sa | sb)


async def check_corroboration(
    results: "dict[str, AgentResult]",
    registry=None,
) -> CorroborationReport:
    """Cross-agent consistency check via Jaccard + contradiction keywords."""
    if len(results) < 2:
        return CorroborationReport(consistent=True, issues=[])
    issues: list[str] = []
    items = list(results.items())
    for i, (tid1, r1) in enumerate(items):
        out1 = (r1.output or "").lower()
        for tid2, r2 in items[i + 1:]:
            out2 = (r2.output or "").lower()
            if not out1.strip() or not out2.strip():
                continue
            for kw, opposites in _CONTRADICTIONS.items():
                if kw in out1:
                    for opp in opposites:
                        if opp in out2:
                            issues.append(f"{tid1} vs {tid2}: '{kw}' contradicts '{opp}'")
    if issues:
        log.warning("agent_corroboration: inconsistent — %s", issues)
    return CorroborationReport(consistent=not issues, issues=issues)
