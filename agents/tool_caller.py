"""GOAT 2.0 — ToolCallerAgent: file and working memory tool orchestration for the DAG."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from config.settings import ModelSpec, Settings
from .base_agent import BaseAgent

if TYPE_CHECKING:
    # Cross-module type hints only — keeps agents/ decoupled at runtime.
    from config.agent_types import AgentResult, AgentTask

log = logging.getLogger("goat2.agents.tool_caller")

__all__ = ["ToolCallerAgent"]

_SYSTEM_PROMPT = """\
You are a tool orchestration agent in GOAT 2.0, a multi-agent AI system.

Your role is to execute file operations and working memory queries on behalf of the DAG.
You operate exclusively in the working tier (Redis, dag:* namespace).

Available tools:
- File (8): file_read, file_write, file_create, file_list, file_search,
  file_grep, file_info, file_read_lines
- Memory (working tier only): memory_recent, memory_get, memory_store, memory_search

Workspace root: /home/lenovo/workspace/goat2
All file paths must be relative to workspace root or absolute starting with /home/lenovo/workspace/goat2.
Never use /workspace, /dag, or / as path.

Rules:
- Evaluate task semantics to decide which tools are needed — do not wait for explicit commands
- Use file tools for file operations; use memory tools to check or store DAG context
- Do not access ChromaDB or Letta — working tier only (dag:* namespace)
- Say 'tool not connected' if a tool returns an ERROR response\
"""


class ToolCallerAgent(BaseAgent):
    """
    Executes file operations and working memory queries on behalf of the DAG.

    Tools: 8 file tools + 4 DAG memory tools (working tier — Redis, dag:* namespace).
    Requires spec.tool_calling=True; raises RuntimeError at execute() time otherwise.
    Default model: deepseek-chat (tool_calling=True).
    Override: ToolCallerAgent(spec=get_model("gpt-4o-mini"))
    """

    role = "tool_caller"

    def __init__(self, spec: ModelSpec | None = None) -> None:
        # Lazy import breaks any agent ↔ tools/ ↔ agents.base_agent cycle
        from tools import (
            FILE_READ, FILE_WRITE, FILE_CREATE, FILE_LIST,
            FILE_SEARCH, FILE_GREP, FILE_INFO, FILE_READ_LINES,
            MEMORY_RECENT_DAG, MEMORY_GET_DAG, MEMORY_STORE_DAG, MEMORY_SEARCH_DAG,
        )
        _tools = [
            FILE_READ, FILE_WRITE, FILE_CREATE, FILE_LIST,
            FILE_SEARCH, FILE_GREP, FILE_INFO, FILE_READ_LINES,
            MEMORY_RECENT_DAG, MEMORY_GET_DAG, MEMORY_STORE_DAG, MEMORY_SEARCH_DAG,
        ]
        super().__init__(
            spec=spec or Settings().agents.get("tool_caller"),
            system_prompt=_SYSTEM_PROMPT,
            temperature=0.1,
            tools=_tools,
        )
        log.debug("%s ready spec=%s tools=%s", self.__class__.__name__, self.spec, self.tool_names)

    async def execute(
        self,
        task: AgentTask,
        context: dict[str, AgentResult],
    ) -> str:
        """Execute file and memory tools for the given task.

        Raises:
            RuntimeError: If the configured model has tool_calling=False.
        """
        log.debug("%s.execute start task_id=%s prompt_len=%d", self.__class__.__name__, task.id, len(task.prompt))
        if not self.spec.tool_calling:
            raise RuntimeError(
                f"tool_caller model '{self.spec.model_id}' has tool_calling=False; "
                "use deepseek-chat or gpt-4o-mini."
            )
        messages = self._build_messages(task, context)
        output = await self._chat(messages)
        log.debug("%s.execute done task_id=%s output_len=%d", self.__class__.__name__, task.id, len(output))
        return output
