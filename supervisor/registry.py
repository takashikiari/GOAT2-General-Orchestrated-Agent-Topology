"""Agent registry for GOAT 2.0 supervisor.

MEMORY ACCESS:
==============
DAG agents (planner, researcher, coder, critic, summarizer, tool_caller)
access only Redis via task.memory_manager.working.
They CANNOT access ChromaDB or Letta — supervisor-only.

DAG ↔ SUPERVISOR:
=================
DAG output → Redis (store_dag_result) → Supervisor (retrieve_dag_result)
"""
from __future__ import annotations

import logging

from config.settings import get_model
from supervisor.types import AgentRunner, AgentTask, AgentResult
from supervisor.llm_utils import _call_llm, _format_dep_context
from supervisor.planner import _run_planner
from supervisor.runners import _run_researcher, _run_coder, _run_critic, _run_summarizer, _run_tool_caller

log = logging.getLogger("goat2.supervisor")

__all__ = ["AgentRegistry"]


class AgentRegistry:
    """
    Dynamic registry of GOAT agent runners keyed by role name.
    Runners are async callables: (AgentTask, dep_results) -> str.
    """

    def __init__(self) -> None:
        self._runners: dict[str, AgentRunner] = {}

    def register(self, role: str, runner: AgentRunner) -> None:
        """Register runner under role, replacing any prior registration."""
        self._runners[role] = runner
        log.debug("Registered agent: %s", role)

    def get(self, role: str) -> AgentRunner:
        if role not in self._runners:
            raise KeyError(
                f"No agent registered for role '{role}'. "
                f"Available: {list(self._runners)}"
            )
        return self._runners[role]

    def has(self, role: str) -> bool:
        return role in self._runners

    def roles(self) -> list[str]:
        return list(self._runners)

    def make_and_register(
        self,
        role: str,
        model_key: str,
        system_prompt: str,
    ) -> AgentRunner:
        """
        Factory: build a simple LLM runner from a model key + system prompt
        and register it. Returns the runner for further composition.
        """
        spec = get_model(model_key)

        async def _runner(task: AgentTask, dep_results: dict[str, AgentResult]) -> str:
            task.source = "generated"
            context = _format_dep_context(dep_results)
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"{context}\n\nTask: {task.prompt}".strip()},
            ]
            return await _call_llm(spec, messages)

        _runner.__name__ = f"agent_{role}"
        self.register(role, _runner)
        return _runner


def _build_default_registry() -> AgentRegistry:
    """Construct the default AgentRegistry with 6 built-in DAG runners.

    Note: "memory" is NOT an agent runner — it's a ModelSpec key used by
    supervisor for behavioral learning, classification, and language detection.
    DAG output is stored to Redis via store_dag_result() and read by supervisor.
    """
    registry = AgentRegistry()
    registry.register("planner",     _run_planner)
    registry.register("researcher",  _run_researcher)
    registry.register("coder",       _run_coder)
    registry.register("critic",      _run_critic)
    registry.register("summarizer",  _run_summarizer)
    registry.register("tool_caller", _run_tool_caller)
    return registry
