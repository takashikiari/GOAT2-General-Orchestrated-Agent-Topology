"""
memory.layers — Backend Mapper: L0-L3 logical layers → physical tiers
(Permanent / Working / Episodic). GOAT/Orchestrator talk ONLY to this class.

    L0 Identity / L1 Facts → PermanentMemory
    L2 Working / Session Cache → WorkingMemory / SessionCache
    L3 Episodic / L2.5 Activation → EpisodicMemory / ActivationStore

Steps 1-6: mapping, session cache, retrieval budget, L3 write, AITS dynamic
budget + async prefetch (``assemble_context``).
"""
from __future__ import annotations

import asyncio
import hashlib
import time

from memory.activation import Activation, ActivationStore
from memory.auto_promote import schedule_auto_promote
from memory.budget import enforce_result_limit
from memory.config import (
    ACTIVATION_TTL_SECONDS,
    IDENTITY_BASE_PROMPT,
    L3_GAP_SIGNIFICANCE,
    MAX_CONTEXT_TOKENS,
)
from memory.context_assembler import assemble_blocks
from memory.date_format import prefix_with_date
from memory.episodic import EpisodicMemory
from memory.permanent import PermanentMemory
from memory.promote import promote_fact as _promote_fact
from memory.session_cache import SessionCache
from memory.working import WorkingMemory
from utils.logging.setup import get_logger

log = get_logger(__name__)

# Cache-key namespace for episodic searches. Prefixed onto the query digest
# so search caches are distinguishable from tool-output caches inside the
# session cache.
_SEARCH_NAMESPACE = "search"

_BASE_IDENTITY = IDENTITY_BASE_PROMPT


class MemoryLayers:
    """Backend Mapper — L0-L3 logical layers → physical tiers.

    GOAT/Orchestrator talk only to this class. Step 6 = AITS dynamic budget +
    async prefetch (``assemble_context``; L2 protected to ``L2_CONTEXT_CAP``,
    L3 AITS-gated), Step 5 = L3 write (``store_episodic``), Step 2 = session
    cache.
    """

    def __init__(
        self,
        working: "WorkingMemory",
        episodic: "EpisodicMemory",
        permanent: "PermanentMemory",
        cache_ttl: int = 300,
        extractor=None,
        bm25=None,
        reranker=None,
    ) -> None:
        self._working = working
        self._episodic = episodic
        self._permanent = permanent
        self._extractor = extractor
        self._bm25 = bm25
        self._reranker = reranker
        self._cache = SessionCache(working, ttl_seconds=cache_ttl)
        self._activation = ActivationStore(working, ttl_seconds=ACTIVATION_TTL_SECONDS)
        # Background tasks (auto_promote) tracked for clean shutdown drain.
        self._pending_bg: set[asyncio.Task] = set()

    async def get_identity_and_facts(self) -> dict[str, str]:
        """L0 + L1: identity and critical facts. L0 always loads; L1 degrades to ``{}``.

        L0 identity is emitted unconditionally. L1 facts come from permanent
        memory; if that tier is unreachable or returns malformed data, an empty
        dict is returned (logged) rather than crashing the turn — L0+L2+L3 can
        still assemble without L1. Always loaded, never searched.
        """
        try:
            return await self._permanent.get_all_facts()
        except Exception as exc:  # noqa: BLE001 — L1 is best-effort, never fatal
            log.warning("PermanentMemory unavailable, L1 facts empty: %s", exc)
            return {}

    async def get_identity_prompt(self) -> str:
        """L0: return Letta identity override if set, else fall back to config base_prompt.

        Never raises — any Letta failure returns the config prompt so every turn
        has a guaranteed identity even when the permanent tier is unreachable.
        """
        try:
            override = await self._permanent.get_identity_override()
            if override:
                return override
        except Exception:  # noqa: BLE001 — L0 is always guaranteed
            pass
        return _BASE_IDENTITY

    async def set_identity_override(self, text: str) -> None:
        """Write a new L0 identity override to Letta.

        Passing an empty string clears the override so the config prompt
        is used again. Raises if Letta is unavailable.
        """
        await self._permanent.set_identity_override(text)

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

    async def append_and_save_working_context(
        self, chat_id: str, *messages_to_append: dict,
    ) -> None:
        """L2: atomic read → append → save under the per-chat lock, then schedule auto-promote.

        Holds ``chat_lock`` for the entire read-modify-write so a concurrent
        auto-promote task cannot overwrite messages appended here.  The
        auto-promote task is fired *after* the lock is released so it sees the
        updated message list and holds the same lock for its own cycle.
        """
        async with self._working.chat_lock(chat_id):
            messages = await self._working.get_messages_raw(chat_id)
            messages.extend(messages_to_append)
            await self._working.save_messages_raw(chat_id, messages)
        task = schedule_auto_promote(
            chat_id, self._working, episodic=self._episodic, extractor=self._extractor,
            cache_clear_fn=lambda: self._cache.clear(chat_id),
        )
        self._pending_bg.add(task)
        task.add_done_callback(self._pending_bg.discard)

    async def search_episodic(
        self, query: str, limit: int = 5,
        after: float | None = None, before: float | None = None,
        topic_id: str | None = None,
        chat_id_filter: str | None = None,
    ) -> list[dict]:
        """L3 (uncached): semantic search with optional timestamp filter.

        For fresh results (explicit user request); cached path is
        ``search_episodic_with_cache``. Maps to ``EpisodicMemory.search``,
        returns ``{"content","metadata"}`` closest-first, capped to
        ``MAX_RESULTS_PER_SEARCH``.

        ``chat_id_filter`` restricts results to entries from a specific chat
        session; passed through to ``EpisodicMemory.search``.
        """
        results = await self._episodic.search(
            query, limit=limit, after=after, before=before,
            topic_id=topic_id, chat_id_filter=chat_id_filter,
        )
        return enforce_result_limit(results)

    async def store_episodic(
        self, chat_id: str, content: str, tags: list[str] | None = None,
        topic_id: str = "", doc_id: str | None = None,
    ) -> str:
        """L3: write content to episodic memory — the only L3 write path.

        Returns the doc_id used (UUID). ``doc_id`` may be pre-generated by the
        orchestrator to create an L2↔L3 link before the async write completes.

        Args:
            chat_id: Origin chat — labels the entry for per-chat recency.
            content: The information to store.
            tags: Optional retrieval tags; joined into one metadata string.
            topic_id: Topic thread ID; written to metadata when non-empty so
                future topic-filtered searches can narrow to this thread.
            doc_id: Optional pre-generated doc_id; a new UUID is used if omitted.
        """
        now = time.time()
        metadata: dict = {
            "tags": ",".join(tags or []),
            "timestamp": now,
            "access_count": 0,
            "last_accessed_ts": now,
        }
        if topic_id:
            metadata["topic_id"] = topic_id
        prefixed = prefix_with_date(content, now)
        doc_id = await self._episodic.store(chat_id, prefixed, metadata, doc_id=doc_id)
        if self._bm25 is not None:
            # EpisodicMemory.store stamps message_id on its OWN internal metadata
            # copy right before writing to Chroma — that stamp never propagates
            # back to `metadata` above, so it must be added here explicitly or
            # every BM25-indexed copy of this memory permanently lacks
            # message_id, breaking result_merger._result_id dedup identity.
            self._bm25.add_doc(
                doc_id, prefixed, {**metadata, "chat_id": chat_id, "message_id": doc_id},
            )
        task = asyncio.create_task(self._enrich_at_write(doc_id, content))
        self._pending_bg.add(task)
        task.add_done_callback(self._pending_bg.discard)
        return doc_id

    async def _enrich_at_write(self, doc_id: str, content: str) -> None:
        """Fire-and-forget: enrich a freshly-written L3 entry immediately.

        Real corpus measurement (2026-07-08): enrichment previously only ran
        at L2-trim time (auto_promote), which reached ~10% of stored entries
        — entity_boost reads meta.get("entities"), so it was silently inert
        for the other ~90%. Reuses enrich_l3_entry's existing (user_msg,
        assistant_msg) signature with content as the sole message — same
        importance/entity-extraction math, just not split across two turns.
        """
        from memory.enrichment import enrich_l3_entry
        await enrich_l3_entry(
            doc_id, content, "", self._episodic, self._extractor, bm25=self._bm25,
        )

    async def find_by_keys(
        self, chat_id: str, keys: list[str], limit: int = 15,
    ) -> list[dict]:
        """L3 specific-key retrieval: exact structural matches, scoped to ``chat_id``.

        Maps to ``EpisodicMemory.find_by_keys`` (UUID get-by-id + content
        ``$contains``). Not currently used by the prefetch daemon (specific-key mechanism removed in Task 6); retained for potential future use or on-demand key lookups.
        Results carry ``score = 0.0`` so the merger treats them as exact matches.
        """
        return await self._episodic.find_by_keys(chat_id, keys, limit=limit)

    async def bump_access(self, chat_id: str, ids: list[str]) -> None:
        """L3: best-effort retrieval-popularity bump (access_count, last_accessed_ts).

        Fire-and-forget from the prefetch daemon; never raises. Feeds the
        access-count term of the merge score on future turns.
        """
        await self._episodic.bump_access(chat_id, ids)

    async def bm25_search(self, query: str, limit: int = 15) -> list[dict]:
        """BM25 lexical search; no-op (returns []) when index unavailable.

        Results lack a ``score`` key so ``result_merger`` assigns 0 similarity —
        recency + access terms still fire, and the cross-encoder reranker makes
        the final relevance call.
        """
        if self._bm25 is None:
            return []
        return await self._bm25.search(query, limit=limit)

    async def rerank(self, query: str, results: list[dict]) -> list[dict]:
        """Cross-encoder rerank; no-op (returns results unchanged) when unavailable.

        When the reranker is available, blended_score is overwritten with
        sigmoid(cross_encoder_logit) ∈ (0, 1) so the gap filter in
        ``assemble_context`` always sees a well-scaled distribution.
        """
        if self._reranker is None or not results:
            return results
        return await self._reranker.rerank(query, results)

    async def extract_query_entities(self, query: str) -> dict:
        """Extract GLiNER entities from query; no-op fallback when extractor unavailable."""
        if not self._extractor:
            return {"entities": [], "entity_types": [], "memory_type": "conversation"}
        return await self._extractor.extract(query)

    async def boost_by_entities(
        self, query: str, results: list[dict], pre_extracted: dict | None = None,
    ) -> list[dict]:
        """Re-score results by GLiNER entity overlap; no-op when extractor unavailable."""
        if not self._extractor or not results:
            return results
        from memory.entity_boost import entity_boost
        return await entity_boost(query, results, self._extractor, pre_extracted=pre_extracted)

    async def promote_fact(self, key: str, value: str) -> str:
        """L1: promote a stable fact into the Letta core-memory ``facts`` block.

        Delegates to ``memory.promote.promote_fact`` (upsert-by-key, cap-guarded
        to ``L1_FACTS_MAX_TOKENS`` so L1 stays small/curated). GOAT invokes this
        via the ``promote_memory`` tool — distinct from ``store_episodic`` (L3,
        grows freely) because L1 is permanent, always-in-context, and bounded.
        Returns a status string (never raises).
        """
        return await _promote_fact(self._permanent, key, value)

    async def get_l1_facts(self) -> dict[str, str]:
        """L1: return all stored facts."""
        return await self._permanent.get_all_facts()

    async def delete_l1_fact(self, key: str) -> bool:
        """L1: delete a fact by key. Returns True if it existed."""
        return await self._permanent.delete_fact(key)

    async def get_layer_counts(self, chat_id: str) -> dict:
        """Return entry counts for L1, L2, and L3 (global + per-chat)."""
        facts, messages, l3_total, l3_chat = await asyncio.gather(
            self._permanent.get_all_facts(),
            self._working.get_messages(chat_id),
            self._episodic.count(),
            self._episodic.count(chat_id),
        )
        return {
            "l1_facts": len(facts),
            "l2_messages": len(messages),
            "l3_total": l3_total,
            "l3_this_chat": l3_chat,
        }

    async def search_episodic_with_cache(
        self, chat_id: str, query: str, limit: int = 5,
        topic_id: str | None = None,
        chat_id_filter: str | None = None,
    ) -> tuple[list[dict], bool, str]:
        """L3 + session cache: semantic search, served from the cache on repeat.

        Returns ``(results, cache_hit, cache_key)``: the results, whether they
        came from the cache, and the deterministic key (so the orchestrator can
        report it in observability). Key is ``search:{sha256(query+topic_id+chat_id_filter)[:16]}``
        — SHA-256 (not Python's randomised ``hash``) for cross-restart stability.
        Different ``topic_id`` or ``chat_id_filter`` values produce distinct cache
        keys so scoped and global searches never share an entry. Results capped to
        ``MAX_RESULTS_PER_SEARCH`` before caching so cache hits need no re-cap.
        """
        cache_key = self._search_cache_key(query, topic_id, chat_id_filter)
        cached = await self._cache.get(chat_id, cache_key)
        if cached is not None:
            return cached["results"], True, cache_key
        log.debug("episodic search (cache miss) chat=%s query=%r", chat_id, query[:80])
        results = enforce_result_limit(
            await self._episodic.search(
                query, limit=limit, topic_id=topic_id, chat_id_filter=chat_id_filter,
            )
        )
        await self._cache.set(chat_id, cache_key, {"results": results})
        return results, False, cache_key

    @staticmethod
    def _search_cache_key(
        query: str, topic_id: str | None = None, chat_id_filter: str | None = None,
    ) -> str:
        """Deterministic session-cache key for an episodic query: ``search:{digest}``.

        ``topic_id`` and ``chat_id_filter`` are included in the digest so searches
        with different scopes never collide in the session cache.
        """
        key_str = query + (topic_id or "") + (chat_id_filter or "")
        digest = hashlib.sha256(key_str.encode("utf-8")).hexdigest()[:16]
        return f"{_SEARCH_NAMESPACE}:{digest}"

    async def get_cache(self, chat_id: str, key: str) -> dict | None:
        """Session cache: retrieve a cached value, or ``None`` on miss/expiry."""
        return await self._cache.get(chat_id, key)

    async def set_cache(self, chat_id: str, key: str, value: dict) -> None:
        """Session cache: store ``value`` under ``key`` with the configured TTL."""
        await self._cache.set(chat_id, key, value)

    async def invalidate_cache(self, chat_id: str, key: str) -> None:
        """Session cache: drop a single cache entry (no-op if absent)."""
        await self._cache.invalidate(chat_id, key)

    async def clear_cache(self, chat_id: str) -> None:
        """Session cache: drop every cache entry for ``chat_id`` (SCAN-based)."""
        await self._cache.clear(chat_id)

    async def cache_exists(self, chat_id: str, key: str) -> bool:
        """Session cache: report whether a cache entry exists without reading it."""
        return await self._cache.exists(chat_id, key)

    # --- L2.5 activation layer (per-chat thread state) -----------------------

    async def get_activation(self, chat_id: str) -> Activation | None:
        """L2.5: retrieve the chat's thread activation, or ``None`` if absent."""
        return await self._activation.get(chat_id)

    async def set_activation(self, chat_id: str, activation: Activation) -> bool:
        """L2.5: compare-and-set the chat's thread activation under the cleanup TTL.

        Returns ``False`` (write rejected as stale) if a currently-stored
        activation is newer — see ``ActivationStore.set``.
        """
        return await self._activation.set(chat_id, activation)

    async def clear_activation(self, chat_id: str) -> None:
        """L2.5: drop the chat's thread activation (no-op if absent)."""
        await self._activation.clear(chat_id)

    async def embed_query(self, query: str) -> list[float] | None:
        """Embed ``query`` via the episodic tier's own embedding function.

        Delegates to ``EpisodicMemory.embed_query`` — the same model the semantic
        search uses, so the thread centroid lives in the retrieval's vector
        space. Returns ``None`` on any failure (callers treat that as a cold
        turn); never raises.
        """
        return await self._episodic.embed_query(query)

    async def drain_background(self, timeout: float = 5.0) -> None:
        """Await in-flight auto_promote tasks so shutdown loses no enrichment writes."""
        pending = list(self._pending_bg)
        if not pending:
            return
        log.info("draining %d background tasks", len(pending))
        try:
            await asyncio.wait_for(
                asyncio.gather(*pending, return_exceptions=True),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            log.warning("background drain timed out (%.1fs)", timeout)

    async def assemble_context(
        self, chat_id: str, budget: int | None = None,
        l3_results: list[dict] | None = None,
        facts: dict[str, str] | None = None,
        messages: list[dict] | None = None,
        identity_prompt: str | None = None,
        temporal_center: float | None = None,
    ) -> tuple[list[str], int]:
        """Assemble L0-L3 prompt blocks; returns (blocks, l3_used).

        Fetches any None inputs then delegates to context_assembler.assemble_blocks.
        See memory.context_assembler for the full assembly logic. ``temporal_center``
        (midpoint of a parsed date/time window, when the caller has one — see
        orchestrator.py) is forwarded through to fit_search_results.
        """
        if budget is None:
            budget = MAX_CONTEXT_TOKENS
        if facts is None:
            facts = await self.get_identity_and_facts()
        if messages is None:
            messages = await self.get_working_context(chat_id)
        if identity_prompt is None:
            identity_prompt = _BASE_IDENTITY
        return assemble_blocks(
            budget, l3_results, facts, identity_prompt, messages,
            L3_GAP_SIGNIFICANCE, temporal_center,
        )