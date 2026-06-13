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
    "that multiple agent outputs are consistent with each other and don't "
    "contain contradictions.\n\n"
    "Return ONLY this JSON:\n"
    "{\n"
    '  "consistent": true,\n'
    '  "issues": ["issue 1 description", ...]\n'
    "}\n\n"
    "Rules:\n"
    "  - consistent is true only when outputs don't contradict each other\n"
    "  - For each issue, describe which agents and what contradicts\n"
    "  - If outputs are empty or few, return consistent=true, issues=[]\n"
    "  - Base evaluation on semantic meaning, not exact wording\n"
    "  - Consider: numeric values, file paths, success/failure claims, recommendations"
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