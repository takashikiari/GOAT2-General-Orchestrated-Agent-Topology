"""GOAT 2.0 — CriticAgent: rigorously evaluates agent outputs and returns a structured critique."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from config.settings import ModelSpec, Settings
from .base_agent import BaseAgent

if TYPE_CHECKING:
    # Cross-module type hints only — keeps agents/ decoupled at runtime.
    from config.agent_types import AgentResult, AgentTask

log = logging.getLogger("goat2.agents.critic")

__all__ = ["CriticAgent", "run_critic"]

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a critical review agent in GOAT 2.0, a multi-agent AI system.

Your role is to evaluate work produced by other agents rigorously and honestly. \
You are constructive, not destructive — every issue you raise must include a \
concrete, actionable fix. You do not praise work that does not deserve it.

Evaluate on these four dimensions:

1. Correctness — Does the output actually solve the stated problem? \
   Are there bugs, logical errors, or factual mistakes?
2. Completeness — Are edge cases handled? Is anything missing that the task required?
3. Clarity — Can a competent engineer understand and maintain this output without \
   additional explanation?
4. Alignment — Does the output match the original intent, or has it drifted?

Produce your review using exactly this structure:

## Assessment
One paragraph: your overall verdict and the most important thing to know \
about this submission.

## Issues
Numbered list. For each issue include:
- Severity: CRITICAL (blocks acceptance) / MAJOR (should fix) / MINOR (nice to fix)
- Description: what is wrong and why it matters
- Location: specific line, section, or function where the issue appears
- Fix: a concrete, implementable correction

If there are no issues, write "None identified."

## Suggestions
Optional improvements that are not blocking (performance, style, future-proofing). \
Keep this brief — 3 items maximum.

## Verdict
One of: ACCEPT / REVISE / REJECT

Followed by one sentence explaining the verdict.

Rules:
- Be specific; "this is unclear" is not useful — say what is unclear and why
- Do not invent issues that are not present in the submitted work
- CRITICAL issues alone justify REJECT; multiple MAJOR issues justify REVISE\
"""


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class CriticAgent(BaseAgent):
    """Reviews and critiques agent outputs with a structured verdict.

    Output always contains ## Assessment, ## Issues, ## Suggestions, ## Verdict.
    Model is read from GOAT_AGENT_CRITIC_MODEL (falls back to MODEL_NAME).
    """

    role = "critic"

    def __init__(self, spec: ModelSpec | None = None) -> None:
        super().__init__(
            spec=spec or Settings().agents.get("critic"),
            system_prompt=_SYSTEM_PROMPT,
            temperature=Settings().get_agent_temperature("critic", default=0.2),
        )
        log.debug("%s ready spec=%s tools=%s", self.__class__.__name__, self.spec, self.tool_names)

    async def execute(
        self,
        task: AgentTask,
        context: dict[str, AgentResult],
    ) -> str:
        """
        Produce a structured critique of the upstream agent outputs.

        The task prompt should name what is being reviewed and against what
        criteria. All prior agent outputs are included as context so the
        critic has the full picture.
        """
        log.debug("%s.execute start task_id=%s prompt_len=%d", self.__class__.__name__, task.id, len(task.prompt))
        messages = self._build_messages(
            task,
            context,
            extra=(
                "Review all prior agent outputs shown above. "
                "Apply every evaluation dimension. "
                "Be specific: cite the exact text or code you are criticising."
            ),
        )
        # Tools not needed — critic works purely from the text in context.
        output = await self._chat(messages, tools=[])
        log.debug("%s.execute done task_id=%s output_len=%d", self.__class__.__name__, task.id, len(output))
        return output

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def extract_verdict(self, critique: str) -> str:
        """
        Parse the ## Verdict section from a critique string.

        Returns one of "ACCEPT", "REVISE", "REJECT", or "UNKNOWN" if the
        section cannot be parsed.
        """
        for line in critique.splitlines():
            upper = line.strip().upper()
            for verdict in ("ACCEPT", "REVISE", "REJECT"):
                if upper.startswith(verdict):
                    return verdict
        return "UNKNOWN"

    def is_blocking(self, critique: str) -> bool:
        """
        Return True if the critique contains at least one CRITICAL issue
        or a REJECT verdict — i.e. the supervisor should not proceed without
        addressing the critique.
        """
        verdict = self.extract_verdict(critique)
        if verdict == "REJECT":
            return True
        return "CRITICAL" in critique.upper()


async def run_critic(
    task: "AgentTask",
    context: dict[str, "AgentResult"],
    registry,
) -> str:
    """Module-level runner — instantiates CriticAgent from the registry and runs it.

    Thin convenience alias; mirrors ``agents.researcher.run_researcher``.
    """
    agent = CriticAgent(spec=registry.settings.agents.get("critic"))
    log.debug("run_critic: task_id=%s spec=%s tools=%s", task.id, agent.spec, agent.tool_names)
    output = await agent.execute(task, context)
    task.source = "generated"
    return output
