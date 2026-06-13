"""Tool execution verifier — Level 1 of the two-level post-DAG verification.

Evaluates whether DAG agents actually invoked tools with correct parameters and
whether the observable outcomes satisfy the DagPrompt's verification_criteria.
All evaluation logic is delegated to a single LLM call — no hardcoded rules,
no severity lists, no pattern matching.

Level 2 (output quality critique) is handled by agents/critique.py with an
enriched plan_ctx produced by dag_execution.py.
"""
from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING

from utils.llm_utils import _call_llm, _extract_json, _format_dep_context

if TYPE_CHECKING:
    from config.registry import Registry
    from supervisor.pipeline.dag_prompt_builder import DagPrompt
    from supervisor.types import AgentResult

log = logging.getLogger("goat2.supervisor.pipeline.tool_verifier")

__all__ = ["VerifierReport", "run_tool_verifier"]

_SYSTEM: str = (
    "You are GOAT's tool execution verifier. Given a list of verification criteria "
    "and the actual tool execution outputs from a DAG run, evaluate whether each "
    "criterion was satisfied.\n\n"
    "Return ONLY this JSON — no prose, no markdown:\n"
    "{\n"
    '  "passed": true,\n'
    '  "score": 0.0,\n'
    '  "findings": ["criterion 1: PASS — <evidence>", ...],\n'
    '  "unmet_criteria": ["<criterion that failed>", ...]\n'
    "}\n\n"
    "Rules:\n"
    "  - passed is true only when ALL criteria are met with clear evidence\n"
    "  - score is the fraction of criteria that passed (0.0–1.0)\n"
    "  - findings has exactly one entry per criterion\n"
    "  - unmet_criteria lists only the failed ones\n"
    "  - Base your evaluation solely on evidence in the outputs — do not assume\n"
    "  - If outputs are empty or unclear, set passed=false, score=0.0\n"
    "  - If no criteria are provided, return passed=true, score=1.0"
)


@dataclasses.dataclass
class VerifierReport:
    """Result of Level 1 tool execution verification.

    Attributes:
        passed: True when every verification criterion is satisfied.
        score: Fraction of criteria that passed (0.0–1.0).
        findings: Per-criterion verdict strings from the LLM.
        unmet_criteria: Criteria that were not satisfied.
        raw: The raw LLM response for audit purposes.
    """

    passed: bool
    score: float
    findings: list[str]
    unmet_criteria: list[str]
    raw: str


def _empty_report() -> VerifierReport:
    """Return an empty report used when criteria are absent (not a failure)."""
    return VerifierReport(passed=True, score=1.0, findings=[], unmet_criteria=[], raw="")


async def run_tool_verifier(
    results: "dict[str, AgentResult]",
    dag_prompt: "DagPrompt",
    registry: "Registry",
) -> VerifierReport:
    """Evaluate tool execution against DagPrompt.verification_criteria via LLM.

    Uses a single LLM call with the critic model. Returns a trivially-passing
    VerifierReport when verification_criteria is empty or on any failure — the
    verifier is non-critical and must never block pipeline completion.

    Args:
        results: Task results from WorkflowGraph.execute().
        dag_prompt: The DagPrompt whose verification_criteria guide evaluation.
        registry: ServiceRegistry for model configuration.

    Returns:
        VerifierReport with pass/fail status, per-criterion findings, and score.
    """
    if not dag_prompt.verification_criteria:
        log.debug("tool_verifier: no criteria — skipping verification")
        return _empty_report()

    spec = registry.settings.agents.get("critic")
    criteria_block = "\n".join(f"- {c}" for c in dag_prompt.verification_criteria)
    outputs_block = _format_dep_context(results)

    try:
        raw = await _call_llm(
            spec,
            [
                {"role": "system", "content": _SYSTEM},
                {
                    "role": "user",
                    "content": (
                        f"Verification criteria:\n{criteria_block}\n\n"
                        f"Tool execution outputs:\n{outputs_block}\n\n"
                        "Evaluate each criterion."
                    ),
                },
            ],
        )
        import re as _re
        clean_raw = _re.sub(r"```(?:json)?
?", "", raw).strip()
        data = _extract_json(clean_raw)
        return VerifierReport(
            passed=bool(data.get("passed", True)),
            score=float(data.get("score", 1.0)),
            findings=list(data.get("findings", [])),
            unmet_criteria=list(data.get("unmet_criteria", [])),
            raw=raw,
        )
    except Exception as exc:
        log.warning("run_tool_verifier: LLM call or parse failed — reporting failure: %s", exc)
        return VerifierReport(
            passed=False,
            score=0.0,
            findings=[],
            unmet_criteria=["verification_criteria"],
            raw=str(exc),
        )
