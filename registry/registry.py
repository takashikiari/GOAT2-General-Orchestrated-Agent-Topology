"""
registry.registry — lightweight dependency-injection container.

ServiceRegistry is constructed explicitly by whoever needs it (orchestrator,
tests, CLI entry points).  There is intentionally no module-level singleton —
callers own the registry lifetime, which makes testing trivial.

Usage:
    from registry.registry import ServiceRegistry

    registry = ServiceRegistry()
    client = registry.llm_client       # AsyncOpenAI, built on first access
    memory = registry.working_memory   # WorkingMemory, built on first access
    epis   = registry.episodic_memory  # EpisodicMemory, built on first access
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from config import settings

if TYPE_CHECKING:
    from openai import AsyncOpenAI
    from memory.working import WorkingMemory
    from memory.episodic import EpisodicMemory


class ServiceRegistry:
    """
    Minimal DI container for GOAT 2.0.

    All services are built lazily on first access — importing the registry
    never triggers network activity or raises on missing configuration.
    """

    def __init__(self) -> None:
        """Create an empty registry.  All services are built on first access."""
        self._llm_client: AsyncOpenAI | None = None
        self._working_memory: WorkingMemory | None = None
        self._episodic_memory: EpisodicMemory | None = None

    @property
    def llm_client(self) -> AsyncOpenAI:
        """
        Return the shared AsyncOpenAI-compatible LLM client.

        Built once; reused for the registry's lifetime.
        """
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
        """
        Return the shared WorkingMemory instance.

        Built once; the underlying Redis client is itself lazily connected
        on first I/O call.
        """
        if self._working_memory is None:
            from memory.working import WorkingMemory  # lazy — avoids import-time I/O
            self._working_memory = WorkingMemory()
        return self._working_memory

    @property
    def episodic_memory(self) -> EpisodicMemory:
        """
        Return the shared EpisodicMemory instance.

        Built once; the ChromaDB client is itself lazily initialised on
        first store/search call.
        """
        if self._episodic_memory is None:
            from memory.episodic import EpisodicMemory  # lazy — avoids import-time I/O
            self._episodic_memory = EpisodicMemory()
        return self._episodic_memory
