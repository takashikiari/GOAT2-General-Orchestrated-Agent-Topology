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
from memory.bm25_index import BM25Index
from memory.config import RERANKER_ENABLED, SESSION_CACHE_TTL
from memory.episodic import EpisodicMemory
from memory.gliner_extractor import GLiNERExtractor
from memory.layers import MemoryLayers
from memory.permanent import PermanentMemory
from memory.reranker import CrossEncoderReranker
from memory.working import WorkingMemory
from plugins.plugin_manager import PluginManager


class ServiceRegistry:
    """Minimal DI container. All services built lazily on first access."""

    def __init__(self, episodic_storage_path: str | None = None) -> None:
        """``episodic_storage_path`` overrides the live ChromaDB path (e.g. benchmark isolation)."""
        self._episodic_storage_path = episodic_storage_path
        self._llm_client: AsyncOpenAI | None = None
        self._working_memory: WorkingMemory | None = None
        self._episodic_memory: EpisodicMemory | None = None
        self._permanent_memory: PermanentMemory | None = None
        self._memory_layers: MemoryLayers | None = None
        self._memory_analytics: MemoryAnalytics | None = None
        self._plugin_manager: PluginManager | None = None
        self._gliner_extractor: GLiNERExtractor | None = None
        self._bm25_index: BM25Index | None = None
        self._reranker: CrossEncoderReranker | None = None

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
            self._episodic_memory = EpisodicMemory(self._episodic_storage_path)
        return self._episodic_memory

    @property
    def permanent_memory(self) -> PermanentMemory:
        """Shared PermanentMemory, Letta client lazily connected on first use."""
        if self._permanent_memory is None:
            self._permanent_memory = PermanentMemory()
        return self._permanent_memory

    @property
    def gliner_extractor(self) -> GLiNERExtractor:
        """Shared GLiNERExtractor; model loads lazily on first extraction call."""
        if self._gliner_extractor is None:
            self._gliner_extractor = GLiNERExtractor()
        return self._gliner_extractor

    @property
    def bm25_index(self) -> BM25Index:
        """Shared BM25Index; index built lazily on first search or explicit warmup."""
        if self._bm25_index is None:
            self._bm25_index = BM25Index(self.episodic_memory)
        return self._bm25_index

    @property
    def reranker(self) -> CrossEncoderReranker | None:
        """Shared CrossEncoderReranker; None when reranking is disabled in config."""
        if not RERANKER_ENABLED:
            return None
        if self._reranker is None:
            self._reranker = CrossEncoderReranker()
        return self._reranker

    @property
    def memory_layers(self) -> MemoryLayers:
        """Shared MemoryLayers (Backend Mapper), built from the three tiers."""
        if self._memory_layers is None:
            self._memory_layers = MemoryLayers(
                self.working_memory, self.episodic_memory, self.permanent_memory,
                cache_ttl=SESSION_CACHE_TTL,
                extractor=self.gliner_extractor,
                bm25=self.bm25_index,
                reranker=self.reranker,
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
