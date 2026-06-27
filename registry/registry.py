"""
registry.registry — lightweight DI container for GOAT 2.0.

No module-level singleton — callers own the registry lifetime.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from config import settings

if TYPE_CHECKING:
    from openai import AsyncOpenAI
    from memory.analytics import MemoryAnalytics
    from memory.layers import MemoryLayers
    from memory.working import WorkingMemory
    from memory.episodic import EpisodicMemory
    from memory.permanent import PermanentMemory


class ServiceRegistry:
    """Minimal DI container. All services built lazily on first access."""

    def __init__(self) -> None:
        self._llm_client: AsyncOpenAI | None = None
        self._working_memory: WorkingMemory | None = None
        self._episodic_memory: EpisodicMemory | None = None
        self._permanent_memory: PermanentMemory | None = None
        self._memory_layers: MemoryLayers | None = None
        self._memory_analytics: MemoryAnalytics | None = None

    @property
    def llm_client(self) -> AsyncOpenAI:
        """Shared AsyncOpenAI-compatible LLM client, built once."""
        if self._llm_client is None:
            from openai import AsyncOpenAI  # lazy — avoids import-time side effects
            self._llm_client = AsyncOpenAI(
                api_key=settings.API_KEY,
                base_url=settings.BASE_URL,
                timeout=httpx.Timeout(settings.TIMEOUT_SECONDS),
            )
        return self._llm_client

    @property
    def working_memory(self) -> WorkingMemory:
        """Shared WorkingMemory, Redis client lazily connected on first I/O."""
        if self._working_memory is None:
            from memory.working import WorkingMemory  # lazy — avoids import-time I/O
            self._working_memory = WorkingMemory()
        return self._working_memory

    @property
    def episodic_memory(self) -> EpisodicMemory:
        """Shared EpisodicMemory, ChromaDB lazily initialised on first use."""
        if self._episodic_memory is None:
            from memory.episodic import EpisodicMemory  # lazy — avoids import-time I/O
            self._episodic_memory = EpisodicMemory()
        return self._episodic_memory

    @property
    def permanent_memory(self) -> PermanentMemory:
        """Shared PermanentMemory, Letta client lazily connected on first use."""
        if self._permanent_memory is None:
            from memory.permanent import PermanentMemory  # lazy — avoids import-time I/O
            self._permanent_memory = PermanentMemory()
        return self._permanent_memory

    @property
    def memory_layers(self) -> MemoryLayers:
        """Shared MemoryLayers (Backend Mapper), built from the three tiers.

        The single façade GOAT and the Orchestrator talk to. Constructed
        lazily on first access from the registry's working/episodic/permanent
        tier instances, which themselves connect to their backends lazily.
        """
        if self._memory_layers is None:
            from memory.config import SESSION_CACHE_TTL  # lazy — avoids import-time I/O
            from memory.layers import MemoryLayers  # lazy — avoids import-time I/O
            self._memory_layers = MemoryLayers(
                self.working_memory, self.episodic_memory, self.permanent_memory,
                cache_ttl=SESSION_CACHE_TTL,
            )
        return self._memory_layers

    @property
    def memory_analytics(self) -> "MemoryAnalytics":
        """Shared MemoryAnalytics aggregator, built once (registry-owned).

        Not a module singleton — the registry owns its lifetime. The
        orchestrator records one observation per turn and logs a report every
        ``ANALYTICS_LOG_INTERVAL`` requests.
        """
        if self._memory_analytics is None:
            from memory.analytics import MemoryAnalytics  # lazy — avoids import cycle
            self._memory_analytics = MemoryAnalytics()
        return self._memory_analytics
