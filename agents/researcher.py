"""ResearcherAgent — deep analysis; tool support driven by ModelSpec.tool_calling."""
from __future__ import annotations

from config.settings import ModelSpec, Settings
from supervisor import AgentResult, AgentTask

from .base_agent import BaseAgent
from .prompts.researcher_prompt import _SYSTEM_PROMPT

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

    async def execute(
        self,
        task: AgentTask,
        context: dict[str, AgentResult],
    ) -> str:
        """Produce a structured research report; suppresses tools when spec.tool_calling is False."""
        messages = self._build_messages(task, context)
        tool_override: list | None = [] if not self.spec.tool_calling else None
        return await self._chat(messages, tools=tool_override)
