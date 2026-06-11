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

__all__ = ["ResearcherAgent"]


class ResearcherAgent(BaseAgent):
    """
    Synthesises deep knowledge for a given research task.

    Default model: deepseek-r1 (ModelSpec.tool_calling=False — tools suppressed automatically).
    Tool suppression is driven by spec.tool_calling, not a hardcoded model list.
    Override: ResearcherAgent(spec=get_model("gpt-4o"))
    """

    role = "researcher"

    def __init__(self, spec: ModelSpec | None = None) -> None:
        super().__init__(
            spec=spec or Settings().agents.get("researcher"),
            system_prompt=_SYSTEM_PROMPT,
            temperature=0.3,
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
