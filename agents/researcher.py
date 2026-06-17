"""ResearcherAgent — deep analysis; tool support driven by ModelSpec.tool_calling."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from config.settings import ModelSpec, Settings
from .base_agent import BaseAgent
from .prompts.researcher_prompt import _SYSTEM_PROMPT

if TYPE_CHECKING:
    # Cross-module type hints only — keeps agents/ decoupled at runtime.
    from config.agent_types import AgentResult, AgentTask

log = logging.getLogger("goat2.agents.researcher")

__all__ = ["ResearcherAgent", "run_researcher"]


class ResearcherAgent(BaseAgent):
    """
    Synthesises deep knowledge for a given research task.

    Default model: deepseek-r1 (ModelSpec.tool_calling=False — tools suppressed automatically).
    Tool suppression is driven by spec.tool_calling, not a hardcoded model list.
    Override: ResearcherAgent(spec=get_model("gpt-4o"))
    """

    role = "researcher"

    def __init__(self, spec: ModelSpec | None = None) -> None:
        from tools import WEB_SEARCH, MEMORY_SEARCH_DAG  # lazy — avoids agent↔tools cycle
        super().__init__(
            spec=spec or Settings().agents.get("researcher"),
            system_prompt=_SYSTEM_PROMPT,
            temperature=Settings().get_agent_temperature("researcher", default=0.2),
            tools=[WEB_SEARCH, MEMORY_SEARCH_DAG],
        )
        log.debug("%s ready spec=%s tools=%s", self.__class__.__name__, self.spec, self.tool_names)

    async def execute(
        self,
        task: AgentTask,
        context: dict[str, AgentResult],
    ) -> str:
        """Produce a structured research report; suppresses tools when spec.tool_calling is False."""
        log.debug("%s.execute start task_id=%s prompt_len=%d", self.__class__.__name__, task.id, len(task.prompt))
        messages = self._build_messages(task, context)
        tool_override: list | None = [] if not self.spec.tool_calling else None
        output = await self._chat(messages, tools=tool_override)
        log.debug("%s.execute done task_id=%s output_len=%d", self.__class__.__name__, task.id, len(output))
        return output


async def run_researcher(
    task: "AgentTask",
    context: dict[str, "AgentResult"],
    registry,
) -> str:
    """Module-level runner — instantiates ResearcherAgent from the registry and runs it.

    Provided so callers (tests, ad-hoc scripts, the supervisor's
    pipeline) can import a single callable symbol rather than
    instantiating the agent class themselves. The supervisor's
    pipeline owns the canonical ``_run_researcher`` in
    ``supervisor/pipeline/runners.py``; this is a thin convenience alias.
    """
    agent = ResearcherAgent(spec=registry.settings.agents.get("researcher"))
    log.debug("run_researcher: task_id=%s spec=%s tools=%s", task.id, agent.spec, agent.tool_names)
    output = await agent.execute(task, context)
    task.source = "net" if agent.spec.tool_calling else "generated"
    return output
