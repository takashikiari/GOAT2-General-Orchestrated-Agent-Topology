"""workflow.routing — maps agent role names to agent classes.

All imports are lazy so this module can be imported without triggering
the full agents/ dependency chain.  Agents become available once
``config.agent_types`` and ``config.settings.ModelSpec`` exist.

Registered roles (kept in sync with ``agents/``):
    planner, researcher, coder, critic, summarizer, tool_caller, memory
"""
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agents.base_agent import BaseAgent

# Maps role name → (module_path, class_name)
_ROLE_MAP: dict[str, tuple[str, str]] = {
    "planner":     ("agents.planner",     "PlannerAgent"),
    "researcher":  ("agents.researcher",  "ResearcherAgent"),
    "coder":       ("agents.coder",       "CoderAgent"),
    "critic":      ("agents.critic",      "CriticAgent"),
    "summarizer":  ("agents.summarizer",  "SummarizerAgent"),
    "tool_caller": ("agents.tool_caller", "ToolCallerAgent"),
    "memory":      ("agents.memory_agent","MemoryAgent"),
}


class AgentRouter:
    """Routes role strings to instantiated agent objects.

    No singleton — instantiate one per workflow run or share across runs
    if the agent configuration is stable.

    Args:
        agent_kwargs: Extra keyword arguments forwarded to every agent
            constructor (e.g. ``{"spec": custom_model_spec}``).
    """

    def __init__(self, agent_kwargs: dict[str, Any] | None = None) -> None:
        self._kwargs = agent_kwargs or {}
        self._cache: dict[str, "BaseAgent"] = {}

    def get(self, role: str) -> "BaseAgent":
        """Return a cached agent instance for ``role``.

        Raises:
            ValueError: If ``role`` is not registered.
            ImportError: If the agent module or its dependencies are not
                importable (e.g. ``config.agent_types`` missing).
        """
        if role not in _ROLE_MAP:
            raise ValueError(
                f"Unknown agent role {role!r}. "
                f"Registered roles: {sorted(_ROLE_MAP)}"
            )
        if role not in self._cache:
            self._cache[role] = self._instantiate(role)
        return self._cache[role]

    def supports(self, role: str) -> bool:
        """Return ``True`` if ``role`` is a known, importable role."""
        if role not in _ROLE_MAP:
            return False
        try:
            self._load_class(role)
            return True
        except (ImportError, AttributeError):
            return False

    @staticmethod
    def registered_roles() -> list[str]:
        """Return a sorted list of all registered role names."""
        return sorted(_ROLE_MAP)

    # ── internal ──────────────────────────────────────────────────────────────

    def _instantiate(self, role: str) -> "BaseAgent":
        cls = self._load_class(role)
        try:
            return cls(**self._kwargs)
        except TypeError as exc:
            raise TypeError(
                f"Failed to instantiate {cls.__name__} with kwargs {self._kwargs}: {exc}"
            ) from exc

    @staticmethod
    def _load_class(role: str) -> type:
        module_path, class_name = _ROLE_MAP[role]
        try:
            module = importlib.import_module(module_path)
        except ImportError as exc:
            raise ImportError(
                f"Cannot import agent module '{module_path}' for role '{role}'. "
                f"Ensure config.agent_types and config.settings.ModelSpec exist. "
                f"Original error: {exc}"
            ) from exc
        cls = getattr(module, class_name, None)
        if cls is None:
            raise AttributeError(
                f"Class '{class_name}' not found in module '{module_path}'"
            )
        return cls
