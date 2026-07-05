"""
registry.registry — lightweight DI container for GOAT 2.0.

No module-level singleton — callers own the registry lifetime.
All services are built lazily on first access so importing this module
has no side effects (no connections opened, no files read).
"""
from __future__ import annotations

from pathlib import Path

import httpx
from openai import AsyncOpenAI

from config import settings
from memory.analytics import MemoryAnalytics
from memory.config import SESSION_CACHE_TTL
from memory.episodic import EpisodicMemory
from memory.layers import MemoryLayers
from memory.permanent import PermanentMemory
from memory.working import WorkingMemory
from plugins.plugin_manager import PluginManager


class ServiceRegistry:
    """Minimal DI container. All services built lazily on first access."""

    def __init__(self) -> None:
        self._llm_client: AsyncOpenAI | None = None
        self._working_memory: WorkingMemory | None = None
        self._episodic_memory: EpisodicMemory | None = None
        self._permanent_memory: PermanentMemory | None = None
        self._memory_layers: MemoryLayers | None = None
        self._memory_analytics: MemoryAnalytics | None = None
        self._plugin_manager: PluginManager | None = None

    @property
    def llm_client(self) -> AsyncOpenAI:
        """Shared AsyncOpenAI-compatible LLM client, built once."""
        if self._llm_client is None:
            from config.settings import get_api_key, _infer_provider
            provider = _infer_provider(settings.BASE_URL)
            self._llm_client = AsyncOpenAI(
                api_key=get_api_key(provider),
                base_url=settings.BASE_URL,
                timeout=httpx.Timeout(settings.TIMEOUT_SECONDS),
            )
        return self._llm_client

    @property
    def working_memory(self) -> WorkingMemory:
        """Shared WorkingMemory, Redis client lazily connected on first I/O."""
        if self._working_memory is None:
            self._working_memory = WorkingMemory()
        return self._working_memory

    @property
    def episodic_memory(self) -> EpisodicMemory:
        """Shared EpisodicMemory, ChromaDB lazily initialised on first use."""
        if self._episodic_memory is None:
            self._episodic_memory = EpisodicMemory()
        return self._episodic_memory

    @property
    def permanent_memory(self) -> PermanentMemory:
        """Shared PermanentMemory, Letta client lazily connected on first use."""
        if self._permanent_memory is None:
            self._permanent_memory = PermanentMemory()
        return self._permanent_memory

    @property
    def memory_layers(self) -> MemoryLayers:
        """Shared MemoryLayers (Backend Mapper), built from the three tiers."""
        if self._memory_layers is None:
            self._memory_layers = MemoryLayers(
                self.working_memory, self.episodic_memory, self.permanent_memory,
                cache_ttl=SESSION_CACHE_TTL,
            )
        return self._memory_layers

    @property
    def memory_analytics(self) -> MemoryAnalytics:
        """Shared MemoryAnalytics aggregator, built once (registry-owned)."""
        if self._memory_analytics is None:
            self._memory_analytics = MemoryAnalytics()
        return self._memory_analytics

    @property
    def plugin_manager(self) -> PluginManager:
        """Shared PluginManager for hot-reload tool plugins, built once."""
        if self._plugin_manager is None:
            plugins_dir = Path(__file__).parent.parent / "tools" / "goat_skills"
            self._plugin_manager = PluginManager(self, plugins_dir)
        return self._plugin_manager
