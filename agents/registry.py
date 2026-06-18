"""AgentRegistry — central role→runner registry for the DAG
agents. Owned by ``ServiceRegistry`` and consumed by every
component that needs to dispatch a task to a named agent role
(``GoatSupervisor`` reads it via ``registry.agent_registry``).

LAYER DEPENDENCY:
    This module lives in ``agents/`` — the same package as the
    runner functions it pre-registers. Because each agent
    module also lives under ``agents/``, importing them here
    is a same-package import (no cross-layer cycle).

    However, the agent modules are imported LAZILY inside
    ``__init__`` (not at module level) so that:
      (a) a failure in one agent does not block the others
          from registering;
      (b) tests can construct an empty registry via
          ``AgentRegistry(pre_register_defaults=False)`` without
          paying the full agent-module import cost;
      (c) the registry stays usable in any environment, even
          one where the agent package is partially broken.

USAGE:
    from agents.registry import AgentRegistry

    reg = AgentRegistry()                       # pre-registers 7 roles
    reg.roles()                                 # → ["planner", "coder", ...]
    runner = reg.get("coder")                   # → async runner function
    reg.register("custom_role", custom_runner)  # add at runtime

AGENTRUNNER TYPE:
    ``AgentRunner`` is defined in ``config/agent_types`` as
    ``Callable[[AgentTask, dict[str, AgentResult]], Awaitable[str]]``.
    Each agent module exports a module-level async function
    with this signature; ``AgentRegistry.get`` returns it
    directly. Callers invoke it as
    ``await runner(task, prior_results)``.
"""
from __future__ import annotations

import importlib
import logging
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from config.agent_types import AgentResult, AgentTask, AgentRunner

log = logging.getLogger("goat2.agents.registry")

__all__ = ["AgentRegistry"]


# The seven DAG-agent roles the registry pre-registers. Stored
# in insertion order so ``roles()`` returns a stable, predictable
# list (downstream code and tests rely on this).
_DEFAULT_ROLES: tuple[str, ...] = (
    "planner",
    "researcher",
    "coder",
    "critic",
    "summarizer",
    "tool_caller",
    "memory",
)


class AgentRegistry:
    """In-memory mapping of role name → DAG agent runner.

    A single instance is owned by ``ServiceRegistry`` and passed
    through the dependency-injection tree. The registry is
    thread-confined to the asyncio loop on which it was
    constructed; sharing it across processes is not supported.

    Attributes:
        _runners: Internal ``dict[str, AgentRunner]`` map.
    """

    __slots__ = ("_runners",)

    def __init__(self, *, pre_register_defaults: bool = True) -> None:
        """Construct the registry, optionally pre-registering the 7 defaults.

        Args:
            pre_register_defaults: When True (the default), the
                seven canonical DAG-agent roles are registered
                immediately via lazy imports. When False, the
                registry starts empty — callers add runners via
                ``register()``. The False path exists for
                tests that want a clean slate without the
                agent-module import cost.

        Notes:
            The default-agent imports happen INSIDE __init__ so
            a single broken agent module never blocks the
            others. Import failures are logged at WARNING and
            skipped — the registry is still usable for the
            successful agents and for custom registrations.
        """
        self._runners: dict[str, "AgentRunner"] = {}
        if pre_register_defaults:
            self._pre_register_defaults()

    # ── Public API ──

    def roles(self) -> list[str]:
        """Return the registered role names, in insertion order.

        Returns:
            A list copy — safe to mutate without affecting the
            registry. The order matches the ``_DEFAULT_ROLES``
            tuple for the default registrations.
        """
        return list(self._runners.keys())

    def get(self, role: str) -> "AgentRunner":
        """Return the runner for ``role``.

        Args:
            role: Role name (e.g. ``"coder"``).

        Returns:
            The async runner callable for the role. Compatible
            with the ``AgentRunner`` protocol
            (``async def f(task, context) -> str``).

        Raises:
            KeyError: When ``role`` is not registered.
        """
        if role not in self._runners:
            raise KeyError(
                f"AgentRegistry.get: unknown role {role!r}. "
                f"Registered: {sorted(self._runners.keys())}"
            )
        return self._runners[role]

    def register(self, role: str, runner: "AgentRunner") -> None:
        """Register or replace a runner for ``role``.

        Re-registering an existing role overwrites the previous
        runner silently. There is no de-duplication — callers
        that need a "first wins" or "last wins" policy can
        check ``role in self._runners`` themselves before
        calling.

        Args:
            role: Role name. Convention is lowercase, no spaces
                (matches the default registration style).
            runner: Async callable matching the ``AgentRunner``
                protocol. Stored by reference; not called at
                registration time.
        """
        if not role or not isinstance(role, str):
            raise ValueError(
                f"AgentRegistry.register: role must be a non-empty str, got {role!r}"
            )
        if not callable(runner):
            raise TypeError(
                f"AgentRegistry.register: runner for {role!r} must be callable, "
                f"got {type(runner).__name__}"
            )
        self._runners[role] = runner  # type: ignore[assignment]
        log.debug(
            "AgentRegistry.register: role=%r runner=%s",
            role, getattr(runner, "__name__", runner),
        )

    def unregister(self, role: str) -> bool:
        """Remove a role. Returns True when the role existed.

        Useful in tests that want to drop a default registration
        before installing a mock.
        """
        return self._runners.pop(role, None) is not None

    def __contains__(self, role: object) -> bool:
        return isinstance(role, str) and role in self._runners

    def __len__(self) -> int:
        return len(self._runners)

    def __repr__(self) -> str:
        return f"AgentRegistry(roles={sorted(self._runners.keys())})"

    # ── Internal helpers ──

    def _pre_register_defaults(self) -> None:
        """Load and register the seven default DAG-agent runners.

        Each role is its own import — a failure in one module
        is logged and skipped without affecting the others.
        The agent modules are loaded via ``importlib`` so a
        missing / broken module surfaces as a logged warning
        rather than a hard ``ImportError`` that takes down the
        entire registry.
        """
        # (role, module, attr) — one entry per default agent.
        # Keep this list aligned with ``_DEFAULT_ROLES``.
        bindings: tuple[tuple[str, str, str], ...] = (
            ("planner",     "agents.planner_decompose", "_run_planner"),
            ("researcher",  "agents.researcher",        "run_researcher"),
            ("coder",       "agents.coder",             "run_coder"),
            ("critic",      "agents.critic",            "run_critic"),
            ("summarizer",  "agents.summarizer",        "run_summarizer"),
            ("tool_caller", "agents.tool_caller",       "run_tool_caller"),
            ("memory",      "agents.memory_agent",      "run_memory"),
        )
        for role, module_name, attr in bindings:
            try:
                mod = importlib.import_module(module_name)
                runner: Callable | None = getattr(mod, attr, None)
                if runner is None:
                    log.warning(
                        "AgentRegistry: %s.%s missing — skipping role %r",
                        module_name, attr, role,
                    )
                    continue
                self._runners[role] = runner  # type: ignore[assignment]
                log.debug(
                    "AgentRegistry: registered role=%r → %s.%s",
                    role, module_name, attr,
                )
            except Exception as exc:  # noqa: BLE001 — one bad agent, others still register
                log.warning(
                    "AgentRegistry: failed to import %s for role %r: %s",
                    module_name, role, exc,
                )
