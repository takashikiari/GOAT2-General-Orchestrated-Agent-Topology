"""ServiceRegistry — Central dependency injection container for GOAT 2.0.

Replaces all module-level singletons with explicit dependency injection.
Instantiated ONCE at application startup, passed to all components.

ARCHITECTURE (routing + TYPE_CHECKING + Registry):
==================================================

GOAT 2.0 is split into three layers that must not import each other at
module level: ``agents/``, ``supervisor/``, ``tools/``. ``config/`` is
the leaf layer — it MAY reach supervisor lazily (function-local import)
but NEVER at module level. The two registries are wired as follows:

  ServiceRegistry (config/registry.py, this module)
    └── agent_registry  ──►  AgentRegistry (agents/registry.py)
                                ├── researcher   → _run_researcher
                                ├── coder        → _run_coder
                                ├── critic       → _run_critic
                                ├── planner      → _run_planner
                                ├── summarizer   → _run_summarizer
                                ├── tool_caller  → _run_tool_caller
                                └── memory       → _run_memory

The cross-layer import is performed inside ``ServiceRegistry.__init__``
(NOT at module level) so importing this file at startup does NOT pull
in supervisor/agents/tools.

USAGE:
    from config.registry import ServiceRegistry
    registry = ServiceRegistry()
    runner = registry.get("researcher")
    supervisor = GoatSupervisor(registry=registry)

SERVICES OWNED:
    - settings:           Settings configuration container
    - working_memory:     WorkingMemoryLayer (Redis-backed)
    - memory_manager:     MemoryManager coordinating all three tiers
    - file_tools:         List of file operation ToolDefinitions
    - memory_tools:       List of memory operation ToolDefinitions
    - dag_memory_tools:   Restricted memory tools for DAG agents
    - goat_skills_tools:  GOAT-only computer-control tools (direct_response)
    - dag_tools:          GOAT (start/query/control/list_dag_sessions)
    - system_tools:       Calculator / shell / think / read_logs
    - dynamic_tools:      starts empty, hot-reload populates
    - agent_models:       AgentModels for per-role model configuration
    - letta_client:       Letta client (long-term memory)
    - agent_registry:     AgentRegistry with all 7 DAG runners pre-registered

THREAD SAFETY:
    Registry is designed for single-threaded asyncio event loop usage.
    Do not share across processes without external synchronization.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from config.settings import Settings
from config.agent_models import AgentModels
from memory.working import WorkingMemoryLayer
from memory.shared import MemoryManager
from memory.working import RedisBackend

if TYPE_CHECKING:
    from agents.base_agent import ToolDefinition
    from agents.registry import AgentRegistry

log = logging.getLogger("goat2.config.registry")

__all__ = ["ServiceRegistry"]


class ServiceRegistry:
    """
    Central dependency injection container for GOAT 2.0.

    Owns all service objects: settings, memory layers, memory manager,
    tool definitions, agent models, and the agent registry. Passed
    explicitly to every component.

    ATTRIBUTES:
    ===========
    - settings:        Settings configuration (env vars + toml)
    - working_memory:  WorkingMemoryLayer (Redis-backed session storage)
    - memory_manager:  MemoryManager coordinating all three tiers
    - file_tools:      List of file operation ToolDefinitions
    - memory_tools:    List of memory operation ToolDefinitions
    - dag_memory_tools: Restricted memory tools for DAG agents
    - goat_skills_tools: GOAT-only computer-control tools (direct_response)
    - agent_models:    AgentModels for per-role model configuration
    - letta_client:    Letta client for long-term memory
    - agent_registry:  AgentRegistry with all 7 DAG runners
    - dag_tools:       GOAT (start/query/control/list_dag_sessions)
    - system_tools:    Calculator / shell / think / read_logs
    - dynamic_tools:   starts empty, hot-reload populates

    THREAD SAFETY:
    ==============
    Registry is designed for single-threaded asyncio event loop usage.
    Do not share across processes without external synchronization.

    EXAMPLE:
    ========
        registry = ServiceRegistry()
        supervisor = GoatSupervisor(registry=registry)
        result = await supervisor.run("Build a REST API")
    """

    __slots__ = (
        "settings",
        "working_memory",
        "memory_manager",
        "file_tools",
        "memory_tools",
        "dag_memory_tools",
        "goat_skills_tools",
        "dag_tools",
        "system_tools",
        "dynamic_tools",
        "agent_models",
        "letta_client",
        "agent_registry",
    )

    def __init__(self, config_path: str = "config/goat.toml") -> None:
        """Initialize ServiceRegistry with all service objects.

        Args:
            config_path: Path to goat.toml configuration file.
                        Defaults to "config/goat.toml" in project root.

        INITIALIZATION ORDER:
        =====================
        1. Settings — loads config from env vars and toml
        2. AgentModels — per-role model configuration
        3. WorkingMemory — Redis-backed session storage
        4. LettaClient — long-term memory client
        5. MemoryManager — coordinates all three memory tiers
        6. Tool lists — imported from tools module
        7. AgentRegistry — all 7 DAG runners (lazy import — supervisor/)

        LOGGING:
        ========
        Logs DEBUG on each major step, INFO summary at completion.
        """
        log.debug("ServiceRegistry: initializing (config_path=%s)", config_path)

        # 1. Settings configuration (env vars + toml)
        self.settings = Settings()
        log.debug("ServiceRegistry: settings ready (model_key=%s)", self.settings.supervisor.model_key)

        # 2. Agent models configuration
        self.agent_models = AgentModels()
        log.debug("ServiceRegistry: agent_models ready (roles=%d)", 7)

        # 3. Working memory layer (Redis-backed by default)
        self.working_memory = WorkingMemoryLayer(backend=RedisBackend())
        log.debug(
            "ServiceRegistry: working_memory ready (backend=%s)",
            type(self.working_memory.backend).__name__,
        )

        # 4. Letta client with LettaConfig for dependency injection
        from memory.letta_client import LettaClient
        self.letta_client = LettaClient(letta_config=self.settings.letta)
        log.debug("ServiceRegistry: letta_client ready (base_url=%s)", self.settings.letta.base_url)

        # 5. Memory manager coordinating all three tiers
        self.memory_manager = MemoryManager(
            working=self.working_memory,
            long_term=self.letta_client,
        )
        log.debug("ServiceRegistry: memory_manager ready (%s)", type(self.memory_manager).__name__)

        # 6. Tool definitions — CLEAR separation:
        #    - file_tools:     DAG agents only
        #    - memory_tools:   GOAT (all three tiers)
        #    - dag_memory_tools: DAG (working tier only)
        #    - goat_skills_tools: GOAT only
        #    - dag_tools:      GOAT (start/query/control/list_dag_sessions)
        #    - system_tools:   calculator / shell / think / read_logs
        #    - dynamic_tools:  starts empty, hot-reload populates
        from tools import FILE_TOOLS, GOAT_SKILLS_TOOLS, MEMORY_TOOLS, DAG_MEMORY_TOOLS
        from tools.dag import make_dag_tools
        from tools.system import CALCULATOR, READ_LOGS, SHELL, THINK

        self.file_tools:       list[ToolDefinition] = FILE_TOOLS
        self.memory_tools:     list[ToolDefinition] = MEMORY_TOOLS
        self.dag_memory_tools: list[ToolDefinition] = DAG_MEMORY_TOOLS
        self.goat_skills_tools: list[ToolDefinition] = GOAT_SKILLS_TOOLS
        # Build dag_tools now so the registry owns one canonical list.
        self.dag_tools:        list[ToolDefinition] = make_dag_tools(
            self.memory_manager, goat_session_id="", supervisor=None,
        )
        # Static snapshot of system tools at boot. The ToolsWatcher
        # can refresh this list in place via update_tools() when a
        # file under tools/system/ changes.
        self.system_tools:     list[ToolDefinition] = [CALCULATOR, SHELL, THINK, READ_LOGS]
        self.dynamic_tools:    list[ToolDefinition] = []
        log.debug(
            "ServiceRegistry: tools ready (file=%d, memory=%d, dag_memory=%d, "
            "goat_skills=%d, dag=%d, system=%d, dynamic=%d)",
            len(self.file_tools), len(self.memory_tools), len(self.dag_memory_tools),
            len(self.goat_skills_tools), len(self.dag_tools), len(self.system_tools),
            len(self.dynamic_tools),
        )

        # 7. Agent registry — lazy import of agents.registry. The
        #    cross-package import is INSIDE __init__ so `import
        #    config.registry` at startup does NOT pull in agents/.
        #    AgentRegistry lives in agents/ because it owns the
        #    role→runner map for the DAG agents (not the supervisor).
        from agents.registry import AgentRegistry
        self.agent_registry: AgentRegistry = AgentRegistry()
        log.debug(
            "ServiceRegistry: agent_registry ready (roles=%s)",
            sorted(self.agent_registry.roles()),
        )

        log.info(
            "ServiceRegistry: initialized successfully — "
            "settings=%s, working_memory=%s, memory_manager=%s, "
            "file_tools=%d, memory_tools=%d, dag_memory_tools=%d, "
            "goat_skills_tools=%d, dag_tools=%d, system_tools=%d, dynamic_tools=%d, "
            "agent_registry=%d runners",
            type(self.settings).__name__,
            type(self.working_memory).__name__,
            type(self.memory_manager).__name__,
            len(self.file_tools),
            len(self.memory_tools),
            len(self.dag_memory_tools),
            len(self.goat_skills_tools),
            len(self.dag_tools),
            len(self.system_tools),
            len(self.dynamic_tools),
            len(self.agent_registry.roles()),
        )

    def get(self, role: str):
        """Get agent runner by role — delegates to the owned AgentRegistry.

        Args:
            role: Agent role name (``researcher``, ``coder``, ``critic``,
                  ``planner``, ``summarizer``, ``tool_caller``, ``memory``).

        Returns:
            The async runner callable registered for the role.

        Raises:
            KeyError: If no runner is registered for the given role.
        """
        log.debug("ServiceRegistry.get: role=%r", role)
        return self.agent_registry.get(role)

    def update_tools(self, category: str, tools: "list[ToolDefinition]") -> None:
        """Replace the tools in ``registry.<category>`` with ``tools`` in place.

        Used by the ``ToolsWatcher`` to hot-reload tool packages
        while keeping every existing reference valid. Mutates the
        list in place (``slot[:] = tools``) rather than
        rebinding the attribute, so callers that captured
        ``registry.memory_tools`` (e.g. ``identity.direct_response``
        binds it to a local) see the new tools on their next
        call without any extra plumbing.

        Args:
            category: Attribute name on the registry, e.g.
                ``memory_tools`` or ``dynamic_tools``. The
                attribute must already be a ``list``.
            tools:    New list of ``ToolDefinition`` objects.

        Unknown categories are logged at WARNING and skipped — a
        future ``tools/<name>/`` package whose slot has not been
        wired into the registry is allowed to coexist without
        breaking the watcher.
        """
        if not hasattr(self, category):
            log.warning(
                "ServiceRegistry.update_tools: unknown slot %r — skipping",
                category,
            )
            return
        slot = getattr(self, category)
        if not isinstance(slot, list):
            log.warning(
                "ServiceRegistry.update_tools: %r is not a list (%s) — skipping",
                category, type(slot).__name__,
            )
            return
        old_names = {getattr(t, "name", "") for t in slot}
        new_names = {getattr(t, "name", "") for t in tools}
        added = sorted(new_names - old_names)
        removed = sorted(old_names - new_names)
        slot[:] = tools
        log.info(
            "ServiceRegistry.update_tools: %s → %d tool(s) (added=%s, removed=%s)",
            category, len(tools), added or "none", removed or "none",
        )

    def __repr__(self) -> str:
        """Return compact representation for debugging."""
        return (
            f"ServiceRegistry(settings={type(self.settings).__name__}, "
            f"working_memory={type(self.working_memory).__name__}, "
            f"memory_manager={type(self.memory_manager).__name__}, "
            f"agent_registry={len(self.agent_registry.roles())} runners, "
            f"tools=file:{len(self.file_tools)}+memory:{len(self.memory_tools)}+"
            f"system:{len(self.system_tools)}+dag:{len(self.dag_tools)}+"
            f"dynamic:{len(self.dynamic_tools)})"
        )
