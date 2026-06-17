"""GOAT 2.0 — MemoryAgent: reads and writes working memory for DAG context persistence."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from config.settings import ModelSpec, Settings
from .base_agent import BaseAgent

if TYPE_CHECKING:
    # Cross-module type hints only — keeps agents/ decoupled at runtime.
    from config.agent_types import AgentResult, AgentTask

log = logging.getLogger("goat2.agents.memory")

__all__ = ["MemoryAgent", "run_memory"]

_SYSTEM_PROMPT = """\
You are a working memory agent in GOAT 2.0, a multi-agent AI system.

Your role is to store and retrieve DAG execution context using working memory tools.
You operate exclusively in the working tier (Redis, dag:* namespace).

Available tools (working tier only):
- memory_recent: retrieve the most recent working memory entries
- memory_get: get a specific working memory entry by key
- memory_store: store a new entry in working memory
- memory_search: search working memory entries by query

Rules:
- Use memory_recent to check existing context before storing new entries
- Use memory_store to persist task outputs for downstream agents
- Use memory_search to find relevant prior context
- Do not access ChromaDB or Letta — working tier only
- Report exactly what was stored or retrieved; do not invent content\
"""


class MemoryAgent(BaseAgent):
    """
    Reads and writes working memory for DAG context persistence.

    Tools: 4 DAG memory tools only (working tier — Redis, dag:* namespace).
    Reuses the tool_caller model (deepseek-chat by default).
    Override: MemoryAgent(spec=get_model("gpt-4o-mini"))
    """

    role = "memory"

    def __init__(self, spec: ModelSpec | None = None) -> None:
        # Lazy import breaks any agent ↔ tools/ ↔ agents.base_agent cycle
        from tools import (
            MEMORY_RECENT_DAG, MEMORY_GET_DAG, MEMORY_STORE_DAG, MEMORY_SEARCH_DAG,
        )
        _tools = [MEMORY_RECENT_DAG, MEMORY_GET_DAG, MEMORY_STORE_DAG, MEMORY_SEARCH_DAG]
        super().__init__(
            spec=spec or Settings().agents.get("tool_caller"),
            system_prompt=_SYSTEM_PROMPT,
            temperature=Settings().get_agent_temperature("memory", default=0.1),
            tools=_tools,
        )
        log.debug("%s ready spec=%s tools=%s", self.__class__.__name__, self.spec, self.tool_names)

    async def execute(
        self,
        task: AgentTask,
        context: dict[str, AgentResult],
    ) -> str:
        """Store or retrieve working memory context for the given task."""
        log.debug("%s.execute start task_id=%s prompt_len=%d", self.__class__.__name__, task.id, len(task.prompt))
        messages = self._build_messages(task, context)
        output = await self._chat(messages)
        log.debug("%s.execute done task_id=%s output_len=%d", self.__class__.__name__, task.id, len(output))
        return output


async def run_memory(
    task: "AgentTask",
    context: dict[str, "AgentResult"],
    registry,
) -> str:
    """Module-level runner — instantiates MemoryAgent from the registry and runs it.

    Thin convenience alias; mirrors ``agents.researcher.run_researcher``.
    """
    agent = MemoryAgent(spec=registry.settings.agents.get("memory"))
    log.debug("run_memory: task_id=%s spec=%s tools=%s", task.id, agent.spec, agent.tool_names)
    output = await agent.execute(task, context)
    task.source = "generated"
    return output
