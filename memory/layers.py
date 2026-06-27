"""
memory.layers — Backend Mapper: L0-L3 logical layers → physical tiers
(Permanent / Working / Episodic). GOAT/Orchestrator talk ONLY to this class.

    L0 Identity / L1 Facts → PermanentMemory
    L2 Working / L2.5 Cache → WorkingMemory / SessionCache
    L3 Episodic           → EpisodicMemory

Steps 1-6: mapping, L2.5 cache, retrieval budget, L3 write, AITS dynamic
budget + async prefetch (``assemble_context``).
"""
from __future__ import annotations

import hashlib
import time
from typing import TYPE_CHECKING

from memory.budget import enforce_result_limit, estimate_tokens
from memory.config import L2_CONTEXT_CAP, MAX_CONTEXT_TOKENS
from memory.session_cache import SessionCache
from utils.logging.setup import get_logger

if TYPE_CHECKING:
    from memory.episodic import EpisodicMemory
    from memory.permanent import PermanentMemory
    from memory.working import WorkingMemory

log = get_logger(__name__)

# Cache-key namespace for episodic searches. Prefixed onto the query digest
# so search caches are distinguishable from tool-output caches inside L2.5.
_SEARCH_NAMESPACE = "search"

# L0 base identity prompt. The always-present root instruction; candidate to
# externalise to config / a Letta-backed identity block in a later step.
_BASE_IDENTITY = "You are a helpful assistant."


class MemoryLayers:
    """Backend Mapper — L0-L3 logical layers → physical tiers.

    GOAT/Orchestrator talk only to this class. Step 6 = AITS dynamic budget +
    async prefetch (``assemble_context``; L2 protected to ``L2_CONTEXT_CAP``,
    L3 AITS-gated), Step 5 = L3 write (``store_episodic``), Step 2 = L2.5 cache.
    """

    def __init__(
        self,
        working: "WorkingMemory",
        episodic: "EpisodicMemory",
        permanent: "PermanentMemory",
        cache_ttl: int = 300,
    ) -> None:
        """
        Store the three physical tier instances and build the L2.5 cache.

        Args:
            working: WorkingMemory instance (backs L2, L2.5).
            episodic: EpisodicMemory instance (backs L3).
            permanent: PermanentMemory instance (backs L0, L1).
            cache_ttl: TTL in seconds for L2.5 cache entries; config supplies
                the active value, this default is a fallback only.
        """
        self._working = working
        self._episodic = episodic
        self._permanent = permanent
        self._cache = SessionCache(working, ttl_seconds=cache_ttl)

    async def get_identity_and_facts(self) -> dict[str, str]:
        """L0 + L1: identity and critical facts.

        Always loaded, never searched. Returns the dict of permanent facts
        as-is (maps to ``PermanentMemory.get_all_facts``).
        """
        return await self._permanent.get_all_facts()

    async def get_working_context(self, chat_id: str) -> list[dict]:
        """L2: current conversation messages for this chat.

        Maps to ``WorkingMemory.get_messages`` — messages sorted by
        timestamp ascending, or an empty list when none exist yet.
        """
        return await self._working.get_messages(chat_id)

    async def save_working_context(self, chat_id: str, messages: list[dict]) -> None:
        """L2: persist updated conversation messages for this chat.

        Maps to ``WorkingMemory.save_messages``. Entries missing a
        ``timestamp`` are stamped at write time by the working tier.
        """
        await self._working.save_messages(chat_id, messages)

    async def search_episodic(self, query: str, limit: int = 5) -> list[dict]:
        """L3 (uncached): semantic search across episodic memory.

        For fresh results (explicit user request); cached path is
        ``search_episodic_with_cache``. Maps to ``EpisodicMemory.search``,
        returns ``{"content","metadata"}`` closest-first, capped to
        ``MAX_RESULTS_PER_SEARCH``.
        """
        results = await self._episodic.search(query, limit=limit)
        return enforce_result_limit(results)

    async def store_episodic(
        self, chat_id: str, content: str, tags: list[str] | None = None,
    ) -> None:
        """L3: write content to episodic memory — the only L3 write path.

        GOAT calls this via the ``store_memory`` tool. Maps to
        ``EpisodicMemory.store``; tags are joined into one metadata string and a
        timestamp added (ChromaDB metadata must be primitives; recency sorts
        by ``metadata.timestamp``).

        Args:
            chat_id: Origin chat — labels the entry for per-chat recency.
            content: The information to store.
            tags: Optional retrieval tags; joined into one metadata string.
        """
        metadata = {"tags": ",".join(tags or []), "timestamp": time.time()}
        await self._episodic.store(chat_id, content, metadata)

    async def search_episodic_with_cache(
        self, chat_id: str, query: str, limit: int = 5,
    ) -> list[dict]:
        """L3 + L2.5: semantic search, served from the session cache on repeat.

        Builds a deterministic key, returns the cached list on a hit, else
        searches episodic, stores, and returns. Key is ``search:{sha256(query)[:16]}``
        — SHA-256 (not Python's randomised ``hash``) for stability across
        restarts/processes. Results capped to ``MAX_RESULTS_PER_SEARCH`` before
        caching so hits return the limited set without re-triggering the cap.
        """
        cache_key = self._search_cache_key(query)
        cached = await self._cache.get(chat_id, cache_key)
        if cached is not None:
            return cached["results"]
        log.debug("episodic search (cache miss) chat=%s query=%r", chat_id, query[:80])
        results = enforce_result_limit(await self._episodic.search(query, limit=limit))
        await self._cache.set(chat_id, cache_key, {"results": results})
        return results

    @staticmethod
    def _search_cache_key(query: str) -> str:
        """Deterministic L2.5 key for an episodic query: ``search:{digest}``."""
        digest = hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]
        return f"{_SEARCH_NAMESPACE}:{digest}"

    async def get_cache(self, chat_id: str, key: str) -> dict | None:
        """L2.5: retrieve a cached value, or ``None`` on miss/expiry."""
        return await self._cache.get(chat_id, key)

    async def set_cache(self, chat_id: str, key: str, value: dict) -> None:
        """L2.5: store ``value`` under ``key`` with the configured TTL."""
        await self._cache.set(chat_id, key, value)

    async def invalidate_cache(self, chat_id: str, key: str) -> None:
        """L2.5: drop a single cache entry (no-op if absent)."""
        await self._cache.invalidate(chat_id, key)

    async def clear_cache(self, chat_id: str) -> None:
        """L2.5: drop every cache entry for ``chat_id`` (SCAN-based)."""
        await self._cache.clear(chat_id)

    async def cache_exists(self, chat_id: str, key: str) -> bool:
        """L2.5: report whether a cache entry exists without reading it."""
        return await self._cache.exists(chat_id, key)

    async def assemble_context(
        self, chat_id: str, budget: int | None = None,
        l3_results: list[dict] | None = None,
    ) -> list[str]:
        """Assemble L0-L3 prompt blocks under a dynamic (AITS) budget.

        L0+L1 mandatory (always kept). L2 (live conversation) is protected: kept
        up to ``L2_CONTEXT_CAP`` by dropping the oldest messages, independent of
        ``budget`` — the live thread is never fully lost (Step 6 fix). L3 is
        AITS-gated: included only when ``l3_results`` is given and budget remains
        after L0+L1+L2, formatted via ``_fit_search_results`` (silent, closest).

        Args:
            chat_id: Current chat session ID.
            budget: AITS per-intent token budget (falls back to
                ``MAX_CONTEXT_TOKENS`` when ``None``).
            l3_results: Prefetched episodic results (closest first) or ``None``.
        """
        if budget is None:
            budget = MAX_CONTEXT_TOKENS
        # L0 + L1: identity + facts — mandatory, never dropped.
        facts = await self.get_identity_and_facts()
        identity = f"[Identity]\n{_BASE_IDENTITY}"
        if facts:
            identity += f"\n\nKnown facts:\n{self._format_facts(facts)}"
        mandatory_tokens = estimate_tokens(identity)
        blocks = [identity]
        # L2: working context — protected, drop-oldest up to L2_CONTEXT_CAP.
        messages = await self.get_working_context(chat_id)
        trimmed = self._trim_recent_messages(messages, L2_CONTEXT_CAP)
        l2_tokens = 0
        if trimmed:
            l2_block = f"[Conversation History]\n{self._format_messages(trimmed)}"
            l2_tokens = estimate_tokens(l2_block)
            blocks.append(l2_block)
        # L3: episodic — AITS-gated, fits the budget remaining after L0+L1+L2.
        if l3_results:
            l3_budget = budget - mandatory_tokens - l2_tokens
            if l3_budget > 0:
                l3_block = self._fit_search_results(l3_results, l3_budget)
                if l3_block:
                    blocks.append(f"[Related Memory]\n{l3_block}")
        return blocks

    @staticmethod
    def _trim_recent_messages(messages: list[dict], max_tokens: int) -> list[dict]:
        """Keep the most recent ``messages`` whose combined tokens fit ``max_tokens``.

        Newest→oldest accumulation; older messages dropped. Returns oldest-first.
        Each message estimated as ``role: content`` to match the block; DEBUG
        on trim.
        """
        if not messages:
            return []
        kept: list[dict] = []
        total = 0
        for msg in reversed(messages):
            tok = estimate_tokens(f"{msg['role']}: {msg['content']}")
            if total + tok > max_tokens and kept:
                break
            kept.append(msg)
            total += tok
        kept.reverse()
        if len(kept) < len(messages):
            log.debug("L2 trimmed %d->%d messages (cap=%d)", len(messages), len(kept), max_tokens)
        return kept

    @staticmethod
    def _fit_search_results(results: list[dict], max_tokens: int) -> str:
        """Format episodic results, closest first, keeping as many as fit ``max_tokens``.

        Greedy add-while-fits; returns joined lines (possibly empty). Silent by
        design — partial recall beats dropping the whole block with a warning.
        """
        lines: list[str] = []
        total = 0
        for r in results:
            line = f"- {r['content']}"
            tok = estimate_tokens(line)
            if total + tok > max_tokens and lines:
                break
            lines.append(line)
            total += tok
        return "\n".join(lines)

    @staticmethod
    def _format_facts(facts: dict[str, str]) -> str:
        """Format L0+L1 facts as ``- key: value`` lines, one per fact."""
        return "\n".join(f"- {key}: {value}" for key, value in facts.items())

    @staticmethod
    def _format_messages(messages: list[dict]) -> str:
        """Format L2 conversation history as ``role: content`` lines, in order."""
        return "\n".join(f"{m['role']}: {m['content']}" for m in messages)