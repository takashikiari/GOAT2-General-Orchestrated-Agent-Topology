"""Agent registry for GOAT 2.0 supervisor.

Self-initializing registry of all 7 DAG agent runners. Each runner is an
async callable (AgentTask, dep_results, registry) -> str.

NO SINGLETON: AgentRegistry is a regular class. Construct as many
instances as you need; the only canonical one lives at
``ServiceRegistry.agent_registry``.

REGISTRATION:
=============
On construction, ``AgentRegistry()`` registers all 7 built-in runners:

    planner      → _run_planner
    researcher   → _run_researcher
    coder        → _run_coder
    critic       → _run_critic
    summarizer   → _run_summarizer
    tool_caller  → _run_tool_caller
    memory       → _run_memory

All 7 runners come from ``supervisor/pipeline/runners.py``.

MEMORY ACCESS:
==============
DAG agents (planner, researcher, coder, critic, summarizer, tool_caller,
memory) access only Redis via task.memory_manager.working. They CANNOT
access ChromaDB or Letta — supervisor-only.

DAG ↔ SUPERVISOR:
=================
DAG output → Redis (store_dag_result) → Supervisor (retrieve_dag_result)
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from supervisor.types import AgentRunner
from supervisor.pipeline.runners import (
    _run_researcher,
    _run_coder,
    _run_critic,
    _run_summarizer,
    _run_tool_caller,
    _run_memory,
)

if TYPE_CHECKING:
    from config.registry import Registry

log = logging.getLogger("goat2.supervisor.registry")

__all__ = ["AgentRegistry"]


class AgentRegistry:
    """
    Dynamic registry of GOAT agent runners keyed by role name.

    On construction, the 7 default runners are registered automatically
    so callers never see an empty registry.

    Runners are async callables: (AgentTask, dep_results, registry) -> str.

    NOT A SINGLETON: instantiate as needed. The canonical instance lives
    at ``ServiceRegistry.agent_registry``.
    """

    def __init__(self) -> None:
        """Initialize with all 7 default runners pre-registered."""
        self._runners: dict[str, AgentRunner] = {}
        self._register_defaults()
        log.debug(
            "AgentRegistry: initialized with %d runners: %s",
            len(self._runners),
            sorted(self._runners),
        )

    def _register_defaults(self) -> None:
        """Register the 7 built-in DAG runners from supervisor/pipeline/runners.py."""
        from agents.planner_decompose import _run_planner  # lazy: agents/ cross-layer
        self.register("planner",     _run_planner)
        self.register("researcher",  _run_researcher)
        self.register("coder",       _run_coder)
        self.register("critic",      _run_critic)
        self.register("summarizer",  _run_summarizer)
        self.register("tool_caller", _run_tool_caller)
        self.register("memory",      _run_memory)

    def register(self, role: str, runner: AgentRunner) -> None:
        """Register runner under role, replacing any prior registration."""
        self._runners[role] = runner
        log.debug("AgentRegistry: registered role=%s runner=%s", role, getattr(runner, "__name__", runner))

    def get(self, role: str) -> AgentRunner:
        """Return the runner registered for ``role``; raises KeyError if missing.

        Logs at DEBUG which role was requested and which runner was returned.
        """
        if role not in self._runners:
            log.debug("AgentRegistry: get(%r) MISS — available=%s", role, sorted(self._runners))
            raise KeyError(
                f"No agent registered for role '{role}'. "
                f"Available: {list(self._runners)}"
            )
        runner = self._runners[role]
        log.debug("AgentRegistry: get(%r) -> %s", role, getattr(runner, "__name__", runner))
        return runner

    def has(self, role: str) -> bool:
        """Return True when a runner is registered for ``role``."""
        return role in self._runners

    def roles(self) -> list[str]:
        """Return the list of currently registered role names."""
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

        Args:
            role: Agent role name to register under.
            model_key: Catalogue key from ``config.model_catalogue.MODELS``.
            system_prompt: System message for the LLM call.

        Returns:
            The newly registered AgentRunner callable.
        """
        # Lazy import: config.settings can be reached without dragging in
        # the rest of the supervisor package at import time.
        from config.settings import get_model
        from utils.llm_utils import _call_llm, _format_dep_context
        from config.agent_types import AgentTask, AgentResult

        spec = get_model(model_key)

        async def _runner(
            task: AgentTask,
            dep_results: dict[str, AgentResult],
            registry: "Registry",
        ) -> str:
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
    """Backward-compatible factory: return a fully populated AgentRegistry.

    Equivalent to ``AgentRegistry()`` since __init__ now self-registers
    all 7 defaults. Kept for callers that imported this helper before
    the self-initializing constructor was introduced.
    """
    return AgentRegistry()

