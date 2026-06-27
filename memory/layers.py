"""
memory.layers — Backend Mapper: translates logical layers (L0-L3) to
physical storage tiers (Permanent / Working / Episodic).

GOAT and the Orchestrator interact ONLY with this class's methods — they
never import the physical tiers directly, so backends (Redis, ChromaDB, Letta)
can be swapped without touching Orchestrator code.

    L0 Identity / L1 Facts → PermanentMemory
    L2 Working / L2.5 Cache → WorkingMemory / SessionCache
    L3 Episodic           → EpisodicMemory

Steps 1-3 added the mapping, L2.5 cache, and retrieval budget; Step 5 adds
the L3 write path (``store_episodic``). No prefetch yet.
"""
from __future__ import annotations

import hashlib
import time
from typing import TYPE_CHECKING

from memory.budget import enforce_context_budget, enforce_result_limit, estimate_tokens
from memory.config import MAX_CONTEXT_TOKENS, MAX_RESULTS_PER_SEARCH
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

# L0 base identity prompt. The always-present root instruction for the agent.
# A candidate to externalise to config / a Letta-backed identity block in a
# later step; kept as a constant here so prepare_context_for_prompt is the
# single source of the assembled system prompt.
_BASE_IDENTITY = "You are a helpful assistant."


class MemoryLayers:
    """Backend Mapper — L0-L3 logical layers → physical tiers.

    GOAT/Orchestrator talk only to this class. Step 1 = mapping, Step 2 =
    L2.5 cache, Step 3 = retrieval budget (``prepare_context_for_prompt``;
    L0+L1 protected, L2/L3 dropped lowest-priority-first), Step 5 = L3 write
    path (``store_episodic``). No intent classification or prefetch yet.
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
            cache_ttl: TTL in seconds for L2.5 cache entries (default 300s).
                The active value comes from config via the registry; this
                default is a fallback only.
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

        Use this when fresh results are required (e.g. an explicit user
        request for current data). For the cached path used by the normal
        per-turn flow, see ``search_episodic_with_cache``. Maps to
        ``EpisodicMemory.search`` and returns ``{"content", "metadata"}``
        dicts, closest first, capped to ``MAX_RESULTS_PER_SEARCH``.
        """
        results = await self._episodic.search(query, limit=limit)
        return enforce_result_limit(results)

    async def store_episodic(
        self, chat_id: str, content: str, tags: list[str] | None = None,
    ) -> None:
        """L3: write content to episodic memory — the only L3 write path.

        GOAT calls this via the ``store_memory`` tool when it decides
        something is worth preserving for future sessions. Maps to
        ``EpisodicMemory.store``. Tags are joined into one metadata string
        and a timestamp is added — ChromaDB metadata must be primitives (no
        lists); ``get_recent``/``get_oldest`` sort by ``metadata.timestamp``.

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

        Flow: build a deterministic cache key from the query, check the L2.5
        cache, return the cached list on a hit, otherwise search episodic
        memory, store the result, and return it.

        The cache key is ``search:{sha256(query)[:16]}``. SHA-256 is used
        (not Python's built-in ``hash``) because ``hash`` is randomised per
        process via ``PYTHONHASHSEED`` — a deterministic digest keeps cache
        keys stable across restarts and across the separate MCP process.
        Results are capped to ``MAX_RESULTS_PER_SEARCH`` before caching so
        cache hits return the limited set without re-triggering the cap.
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

    async def prepare_context_for_prompt(
        self, chat_id: str, user_query: str | None = None,
    ) -> list[str]:
        """L0-L3 context assembly with a combined token budget.

        The SINGLE method the Orchestrator calls to get prompt-ready context
        blocks. It assembles every layer, applies the global token budget to
        the combined list, and returns only the blocks that fit within
        ``MAX_CONTEXT_TOKENS``.

        Priority (most → least important):
            1. L0 (Identity) — base identity prompt; ALWAYS included.
            2. L1 (Critical Facts) — permanent facts; ALWAYS included (folded
               into the L0/L1 identity block, never dropped).
            3. L2 (Working Context) — conversation history; included if it
               exists, dropped only if it cannot fit the remaining budget.
            4. L2.5 (Session Cache) — no standalone block; the cache is used
               transparently inside ``search_episodic_with_cache``.
            5. L3 (Episodic Search) — included only if ``user_query`` is given
               and budget remains; lowest priority, dropped first.

        L0+L1 form one mandatory block and are NEVER dropped. L2 and L3 are
        optional and budgeted: when the combined size exceeds the budget,
        blocks are dropped from the end (L3 before L2).

        Args:
            chat_id: Current chat session ID.
            user_query: Query for the cache-aware episodic search. If given,
                results are added as the L3 block.

        Returns:
            Text blocks ready to inject into the prompt, in priority order.
            The total estimated tokens of all returned blocks is ≤
            ``MAX_CONTEXT_TOKENS``.
        """
        # L0 + L1: identity + facts — mandatory, never dropped.
        facts = await self.get_identity_and_facts()
        identity = f"[Identity]\n{_BASE_IDENTITY}"
        if facts:
            identity += f"\n\nKnown facts:\n{self._format_facts(facts)}"
        mandatory = [identity]

        # L2: working context — optional, higher priority than L3.
        optional: list[str] = []
        messages = await self.get_working_context(chat_id)
        if messages:
            optional.append(f"[Conversation History]\n{self._format_messages(messages)}")
        # L3: episodic search — optional, lowest priority (dropped first).
        if user_query:
            results = await self.search_episodic_with_cache(
                chat_id, user_query, limit=MAX_RESULTS_PER_SEARCH,
            )
            if results:
                optional.append(f"[Related Memory]\n{self._format_search_results(results)}")

        blocks = mandatory + optional
        if estimate_tokens("\n".join(blocks)) <= MAX_CONTEXT_TOKENS:
            return blocks
        # Over budget: protect L0+L1, drop lowest-priority optional blocks first.
        mandatory_tokens = estimate_tokens("\n".join(mandatory))
        kept_optional = enforce_context_budget(
            optional, max_tokens=MAX_CONTEXT_TOKENS - mandatory_tokens,
        )
        return mandatory + kept_optional

    @staticmethod
    def _format_facts(facts: dict[str, str]) -> str:
        """Format L0+L1 facts as ``- key: value`` lines, one per fact."""
        return "\n".join(f"- {key}: {value}" for key, value in facts.items())

    @staticmethod
    def _format_messages(messages: list[dict]) -> str:
        """Format L2 conversation history as ``role: content`` lines, in order."""
        return "\n".join(f"{m['role']}: {m['content']}" for m in messages)

    @staticmethod
    def _format_search_results(results: list[dict]) -> str:
        """Format L3 episodic results as ``- content`` lines, closest first."""
        return "\n".join(f"- {r['content']}" for r in results)