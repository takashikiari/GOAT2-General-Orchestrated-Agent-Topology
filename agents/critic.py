"""
GOAT 2.0 — CriticAgent

Rigorously evaluates work produced by other agents and returns a structured
critique. Defaults to llama-3.3-70b on Groq for fast, high-quality review.
"""

from __future__ import annotations

from config.settings import ModelSpec, Settings
from config.agent_types import AgentResult, AgentTask

from .base_agent import BaseAgent

__all__ = ["CriticAgent"]

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
    """
    Reviews and critiques agent outputs with a structured verdict.

    The output always contains ## Assessment, ## Issues, ## Suggestions,
    and ## Verdict sections. The supervisor uses this output both as
    feedback context and as input to the final synthesis step.

    Default model: llama-3.3-70b via Groq — fast, analytical, cost-effective.
    Override: CriticAgent(spec=get_model("gpt-4o"))
    """

    role = "critic"

    def __init__(self, spec: ModelSpec | None = None) -> None:
        super().__init__(
            spec=spec or Settings().agents.get("critic"),
            system_prompt=_SYSTEM_PROMPT,
            temperature=0.3,  # low: reviews should be consistent, not creative
        )

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
        return await self._chat(messages, tools=[])

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
