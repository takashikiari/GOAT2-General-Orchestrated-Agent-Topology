"""GOAT 2.0 — SummarizerAgent: synthesizes upstream agent outputs into a final answer."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from config.settings import ModelSpec, Settings
from .base_agent import BaseAgent

if TYPE_CHECKING:
    # Cross-module type hints only — keeps agents/ decoupled at runtime.
    from config.agent_types import AgentResult, AgentTask

log = logging.getLogger("goat2.agents.summarizer")

__all__ = ["SummarizerAgent", "run_summarizer"]

_SYSTEM_PROMPT = """\
You are a synthesis agent in GOAT 2.0, a multi-agent AI system.

Your role is to produce a concise, accurate final answer from the outputs of upstream agents.

Rules:
- Report only verified facts present in the prior agent outputs
- Do not infer, approximate, or generate content not present in the context
- If a result is empty or errored, state it was not retrieved — never invent content
- No filler text, apologies, or trailing questions
- If all upstream outputs are empty, state clearly that no results were retrieved\
"""


class SummarizerAgent(BaseAgent):
    """Synthesizes upstream agent outputs into a concise final answer.

    Uses no tools — pure synthesis from context. Reports only verified facts.
    Model is read from GOAT_AGENT_SUMMARIZER_MODEL (falls back to MODEL_NAME).
    """

    role = "summarizer"

    def __init__(self, spec: ModelSpec | None = None) -> None:
        _s = Settings()
        super().__init__(
            spec=spec or _s.agents.get("summarizer"),
            system_prompt=_SYSTEM_PROMPT,
            temperature=_s.get_agent_temperature("summarizer", default=0.2),
            max_tool_rounds=_s.get_agent_tool_rounds("summarizer", default=0),
        )
        log.debug("%s ready spec=%s tools=%s", self.__class__.__name__, self.spec, self.tool_names)

    async def execute(
        self,
        task: AgentTask,
        context: dict[str, AgentResult],
    ) -> str:
        """Synthesize upstream outputs into a final answer; no tools invoked."""
        log.debug("%s.execute start task_id=%s prompt_len=%d", self.__class__.__name__, task.id, len(task.prompt))
        messages = self._build_messages(task, context)
        output = await self._chat(messages, tools=[])
        log.debug("%s.execute done task_id=%s output_len=%d", self.__class__.__name__, task.id, len(output))
        return output


async def run_summarizer(
    task: "AgentTask",
    context: dict[str, "AgentResult"],
    registry,
) -> str:
    """Module-level runner — instantiates SummarizerAgent from the registry and runs it.

    Thin convenience alias; mirrors ``agents.researcher.run_researcher``.
    """
    agent = SummarizerAgent(spec=registry.settings.agents.get("summarizer"))
    log.debug("run_summarizer: task_id=%s spec=%s tools=%s", task.id, agent.spec, agent.tool_names)
    output = await agent.execute(task, context)
    task.source = "generated"
    return output
