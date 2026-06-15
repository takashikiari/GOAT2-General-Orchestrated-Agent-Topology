"""Cross-agent corroboration — verify outputs are consistent across agents.

After DAG agents complete, this module checks whether agent outputs corroborate
each other or contain contradictions. Uses LLM for semantic checking,
not keyword matching.

This runs after agents complete but before the critic to catch issues early.
"""
from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING

from utils.llm_utils import _call_llm

if TYPE_CHECKING:
    from config.registry import Registry
    from supervisor.types import AgentResult

log = logging.getLogger("goat2.supervisor.pipeline.agent_corroboration")

__all__ = ["CorroborationReport", "check_corroboration"]


@dataclasses.dataclass
class CorroborationReport:
    """Outcome of cross-agent corroboration check.

    Attributes:
        consistent: True when outputs are consistent across agents.
        issues: List of inconsistency descriptions.
        raw: Raw LLM response for audit.
    """

    consistent: bool
    issues: list[str]
    raw: str


_SYSTEM: str = (
    "You are GOAT's cross-agent corroboration checker. Your job is to verify "
    "that multiple agent outputs are mutually consistent and do not contain "
    "DIRECT, MEANINGFUL CONTRADICTIONS.\n\n"
    "Return ONLY this JSON:\n"
    "{\n"
    '  "consistent": true,\n'
    '  "issues": ["issue 1 description", ...]\n'
    "}\n\n"
    "Rules — IMPORTANT (only flag REAL contradictions, not differences):\n"
    "  - consistent=true UNLESS at least one output directly contradicts another\n"
    "  - A REAL contradiction = opposite factual claims about the SAME entity "
    "(e.g. 'latency 200ms' vs 'latency 500ms'; 'file exists' vs 'file missing'; "
    "'process running' vs 'process stopped'). Both must be asserted as fact.\n"
    "  - DO NOT flag: different formatting of the same fact, different levels "
    "of detail, different wording, different recommendations, or partial overlap.\n"
    "  - DO NOT flag a missing claim as a contradiction — only an OPPOSITE claim.\n"
    "  - DO NOT flag a single-source claim (one agent says X, others are silent) "
    "as a contradiction; it is just uncorroborated, which is fine.\n"
    "  - DO NOT flag output style, tone, or language (including Romanian) as a contradiction.\n"
    "  - DO NOT flag length or verbosity differences.\n"
    "  - For each REAL contradiction, describe which agents contradict and on what.\n"
    "  - If outputs are empty or only a few, return consistent=true, issues=[].\n"
    "  - Be conservative: when in doubt, prefer consistent=true."
)


def _format_outputs(results: "dict[str, AgentResult]") -> str:
    """Format agent outputs for LLM input."""
    lines = []
    for tid, result in results.items():
        role = result.role
        output = result.output or "(empty)"
        # Truncate long outputs
        if len(output) > 500:
            output = output[:500] + "..."
        lines.append(f"## {tid} ({role})\n{output}")
    return "\n\n".join(lines)


async def check_corroboration(
    results: "dict[str, AgentResult]",
    registry: "Registry",
) -> CorroborationReport:
    """Check if agent outputs corroborate each other.

    Args:
        results: Dictionary of task_id -> AgentResult from DAG execution.
        registry: ServiceRegistry for model access.

    Returns:
        CorroborationReport with consistency status and issues.
    """
    # Skip if too few outputs
    if len(results) < 2:
        return CorroborationReport(consistent=True, issues=[], raw="")

    # Build prompt with all outputs
    outputs_block = _format_outputs(results)

    try:
        spec = registry.settings.agents.get("critic")
        raw = await _call_llm(
            spec,
            [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": outputs_block},
            ],
        )
        # Parse response
        from utils.llm_utils import _extract_json
        data = _extract_json(raw)
        if not data:
            log.warning("agent_corroboration: failed to parse JSON")
            return CorroborationReport(consistent=True, issues=[], raw=raw)

        consistent = bool(data.get("consistent", True))
        issues = list(data.get("issues", []))

        if not consistent:
            log.warning("agent_corroboration: inconsistent — %s", issues)
        else:
            log.debug("agent_corroboration: consistent")

        return CorroborationReport(
            consistent=consistent,
            issues=issues,
            raw=raw,
        )
    except Exception as exc:
        log.warning("agent_corroboration failed: %s", exc)
        return CorroborationReport(consistent=True, issues=[], raw=str(exc))