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
    └── agent_registry  ──►  AgentRegistry (supervisor/registry.py)
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
    from supervisor.registry import AgentRegistry

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
    - agent_models:    AgentModels for per-role model configuration
    - letta_client:    Letta client for long-term memory
    - agent_registry:  AgentRegistry with all 7 DAG runners

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

        # 6. Tool definitions imported from tools module
        #    These remain module constants in tools/__init__.py
        from tools import FILE_TOOLS, MEMORY_TOOLS, DAG_MEMORY_TOOLS

        self.file_tools: list[ToolDefinition] = FILE_TOOLS
        self.memory_tools: list[ToolDefinition] = MEMORY_TOOLS
        self.dag_memory_tools: list[ToolDefinition] = DAG_MEMORY_TOOLS
        log.debug(
            "ServiceRegistry: tools ready (file=%d, memory=%d, dag_memory=%d)",
            len(self.file_tools), len(self.memory_tools), len(self.dag_memory_tools),
        )

        # 7. Agent registry — lazy import of supervisor.registry.
        #    This is the ONLY cross-layer import in this module, and it
        #    is performed inside __init__ (function-local) so that
        #    `import config.registry` at startup does NOT pull in
        #    supervisor/. AgentRegistry's __init__ self-registers all 7
        #    runners, so no further wiring is required here.
        from supervisor.registry import AgentRegistry
        self.agent_registry: AgentRegistry = AgentRegistry()
        log.debug(
            "ServiceRegistry: agent_registry ready (roles=%s)",
            sorted(self.agent_registry.roles()),
        )

        log.info(
            "ServiceRegistry: initialized successfully — "
            "settings=%s, working_memory=%s, memory_manager=%s, "
            "file_tools=%d, memory_tools=%d, dag_memory_tools=%d, "
            "agent_registry=%d runners",
            type(self.settings).__name__,
            type(self.working_memory).__name__,
            type(self.memory_manager).__name__,
            len(self.file_tools),
            len(self.memory_tools),
            len(self.dag_memory_tools),
            len(self.agent_registry.roles()),
        )

    # Optional: conversation history attached per-request by GoatSupervisor
    _history: object = None

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

    def __repr__(self) -> str:
        """Return compact representation for debugging."""
        return (
            f"ServiceRegistry(settings={type(self.settings).__name__}, "
            f"working_memory={type(self.working_memory).__name__}, "
            f"memory_manager={type(self.memory_manager).__name__}, "
            f"agent_registry={len(self.agent_registry.roles())} runners)"
        )
