"""ServiceRegistry — Central dependency injection container for GOAT 2.0.

Replaces all module-level singletons with explicit dependency injection.
Instantiated ONCE at application startup, passed to all components.

USAGE:
    from config.registry import ServiceRegistry
    registry = ServiceRegistry()
    supervisor = GoatSupervisor(registry=registry)

MIGRATION STATUS:
    Phase 1: Registry created, old singletons still exist (backward compat)
    Phase 2: Supervisor accepts registry parameter
    Phase 3: Internal components migrated to use registry
    Phase 4: Old singletons removed — registry now required

SERVICES OWNED:
    - settings: Settings configuration container
    - working_memory: WorkingMemoryLayer (Redis-backed)
    - memory_manager: MemoryManager coordinating all three tiers
    - file_tools: List of file operation ToolDefinitions
    - memory_tools: List of memory operation ToolDefinitions
    - dag_memory_tools: Restricted memory tools for DAG agents
    - agent_models: AgentModels for per-role model configuration

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

log = logging.getLogger("goat2.registry")

__all__ = ["ServiceRegistry"]


class ServiceRegistry:
    """
    Central dependency injection container for GOAT 2.0.

    Owns all service objects: settings, memory layers, memory manager,
    tool definitions, agent models. Passed explicitly to every component.

    ATTRIBUTES:
    ===========
    - settings: Settings configuration (env vars + toml)
    - working_memory: WorkingMemoryLayer (Redis-backed session storage)
    - memory_manager: MemoryManager coordinating all three tiers
    - file_tools: List of file operation ToolDefinitions
    - memory_tools: List of memory operation ToolDefinitions
    - dag_memory_tools: Restricted memory tools for DAG agents
    - agent_models: AgentModels for per-role model configuration

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
        4. MemoryManager — coordinates all three memory tiers
        5. Tool lists — imported from tools module

        LOGGING:
        ========
        Logs INFO message when registry is created successfully.
        """
        log.info("ServiceRegistry: initializing with config_path=%s", config_path)

        # Settings configuration (env vars + toml)
        self.settings = Settings()

        # Agent models configuration
        self.agent_models = AgentModels()

        # Working memory layer (Redis-backed by default)
        self.working_memory = WorkingMemoryLayer(backend=RedisBackend())

        # Letta client with LettaConfig for dependency injection
        from memory.letta_client import LettaClient
        self.letta_client = LettaClient(letta_config=self.settings.letta)

        # Memory manager coordinating all three tiers
        self.memory_manager = MemoryManager(
            working=self.working_memory,
            long_term=self.letta_client,
        )

        # Tool definitions imported from tools module
        # These remain module constants in tools/__init__.py
        from tools import FILE_TOOLS, MEMORY_TOOLS, DAG_MEMORY_TOOLS

        self.file_tools: list[ToolDefinition] = FILE_TOOLS
        self.memory_tools: list[ToolDefinition] = MEMORY_TOOLS
        self.dag_memory_tools: list[ToolDefinition] = DAG_MEMORY_TOOLS

        log.info(
            "ServiceRegistry: initialized successfully — "
            "settings=%s, working_memory=%s, memory_manager=%s, "
            "file_tools=%d, memory_tools=%d, dag_memory_tools=%d",
            type(self.settings).__name__,
            type(self.working_memory).__name__,
            type(self.memory_manager).__name__,
            len(self.file_tools),
            len(self.memory_tools),
            len(self.dag_memory_tools),
        )

    def get(self, role: str):
        """Get agent runner by role — delegates to AgentRegistry."""
        from supervisor.registry import AgentRegistry
        if not hasattr(self, '_agent_registry'):
            self._agent_registry = AgentRegistry()
        return self._agent_registry.get(role)

    def __repr__(self) -> str:
        """Return compact representation for debugging."""
        return (
            f"ServiceRegistry(settings={type(self.settings).__name__}, "
            f"working_memory={type(self.working_memory).__name__}, "
            f"memory_manager={type(self.memory_manager).__name__})"
        )
