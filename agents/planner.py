"""GOAT 2.0 — PlannerAgent: decomposes an objective into an actionable plan for downstream agents."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from config.settings import ModelSpec, Settings
from .base_agent import BaseAgent

if TYPE_CHECKING:
    # Cross-module type hints only — keeps agents/ decoupled at runtime.
    from config.agent_types import AgentResult, AgentTask

log = logging.getLogger("goat2.agents.planner")

__all__ = ["PlannerAgent"]

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a strategic planning agent in GOAT 2.0, a multi-agent AI system.

Your job is to receive an objective and produce a precise, actionable plan \
that other specialised agents will execute step-by-step. You do not implement \
the solution — you define the path to it.

Produce your plan using exactly this structure:

## Objective
One sentence restating the goal in concrete terms.

## Approach
2–4 sentences describing the overall strategy and any key decisions made \
(e.g. chosen technology, algorithm, or architecture).

## Steps
Numbered list. Each step must:
- Name the agent type best suited for it (researcher / coder / critic / summarizer)
- State a specific, verifiable action
- State the expected output of that action

## Dependencies
Which steps depend on which. Use "Step N depends on Step M" format. \
If all steps are independent, write "None".

## Risks & Assumptions
Bullet list of things that could derail the plan and assumptions you are making. \
Be specific — not "may fail" but "may fail if the API is rate-limited".

## Success Criteria
A checklist a reviewer can use to confirm the goal was fully met.

Rules:
- Be concrete and specific throughout; avoid vague instructions
- Do not include steps you cannot justify from the objective
- If the objective is ambiguous, state the interpretation you chose\
"""


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class PlannerAgent(BaseAgent):
    """Produces a structured plan from an objective.

    Downstream agents receive this plan as context so they know the overall
    strategy, their individual step, and what success looks like.
    Model is read from GOAT_AGENT_PLANNER_MODEL (falls back to MODEL_NAME).
    """

    role = "planner"

    def __init__(self, spec: ModelSpec | None = None) -> None:
        super().__init__(
            spec=spec or Settings().agents.get("planner"),
            system_prompt=_SYSTEM_PROMPT,
            temperature=Settings().get_agent_temperature("planner", default=0.2),
        )
        log.debug("%s ready spec=%s tools=%s", self.__class__.__name__, self.spec, self.tool_names)

    async def execute(
        self,
        task: AgentTask,
        context: dict[str, AgentResult],
    ) -> str:
        """
        Produce a structured plan for the given task.

        Any upstream context (e.g. a prior memory retrieval) is included so
        the planner can incorporate known constraints into the plan.
        """
        log.debug("%s.execute start task_id=%s prompt_len=%d", self.__class__.__name__, task.id, len(task.prompt))
        messages = self._build_messages(task, context)
        # Tools not needed for planning — pure reasoning task.
        output = await self._chat(messages, tools=[])
        log.debug("%s.execute done task_id=%s output_len=%d", self.__class__.__name__, task.id, len(output))
        return output
