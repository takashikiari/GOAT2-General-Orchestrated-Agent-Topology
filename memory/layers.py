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

import asyncio
import hashlib
import time
from datetime import datetime

from memory.activation import Activation, ActivationStore
from memory.auto_promote import schedule_auto_promote
from memory.budget import enforce_result_limit, estimate_tokens
from memory.config import (
    ACTIVATION_TTL_SECONDS,
    IDENTITY_BASE_PROMPT,
    L3_GAP_SIGNIFICANCE,
    MAX_CONTEXT_TOKENS,
)
from memory.context_budget import allocate_context_budget
from memory.episodic import EpisodicMemory
from memory.permanent import PermanentMemory
from memory.promote import promote_fact as _promote_fact
from memory.session_cache import SessionCache
from memory.working import WorkingMemory
from utils.logging.setup import get_logger

log = get_logger(__name__)

# Cache-key namespace for episodic searches. Prefixed onto the query digest
# so search caches are distinguishable from tool-output caches inside L2.5.
_SEARCH_NAMESPACE = "search"

# Minimum blended_score for pre-scored L3 results when no structural gap is found.
# Prevents uniformly-mediocre results (old + low-similarity) from being injected.
_BLENDED_MIN_SCORE = 0.35

# L0 base identity prompt — externalised to config ([identity] base_prompt).
_BASE_IDENTITY = IDENTITY_BASE_PROMPT


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
        extractor=None,
    ) -> None:
        """
        Store the three physical tier instances and build the L2.5 cache.

        Args:
            working: WorkingMemory instance (backs L2, L2.5).
            episodic: EpisodicMemory instance (backs L3).
            permanent: PermanentMemory instance (backs L0, L1).
            cache_ttl: TTL in seconds for L2.5 cache entries; config supplies
                the active value, this default is a fallback only.
            extractor: Optional GLiNERExtractor for L3 enrichment at trim time.
        """
        self._working = working
        self._episodic = episodic
        self._permanent = permanent
        self._extractor = extractor
        self._cache = SessionCache(working, ttl_seconds=cache_ttl)
        # L2.5 activation layer — per-chat thread state. Distinct TTL from the
        # search cache: a long cleanup horizon (NOT a reset). See [activation].
        self._activation = ActivationStore(working, ttl_seconds=ACTIVATION_TTL_SECONDS)

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
        schedule_auto_promote(chat_id, self._working, episodic=self._episodic, extractor=self._extractor)

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
        return await self._episodic.store(chat_id, content, metadata, doc_id=doc_id)

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
        """L3 + L2.5: semantic search, served from the session cache on repeat.

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
        """Deterministic L2.5 key for an episodic query: ``search:{digest}``.

        ``topic_id`` and ``chat_id_filter`` are included in the digest so searches
        with different scopes never collide in the L2.5 cache.
        """
        key_str = query + (topic_id or "") + (chat_id_filter or "")
        digest = hashlib.sha256(key_str.encode("utf-8")).hexdigest()[:16]
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

    # --- L2.5 activation layer (per-chat thread state) -----------------------

    async def get_activation(self, chat_id: str) -> Activation | None:
        """L2.5: retrieve the chat's thread activation, or ``None`` if absent."""
        return await self._activation.get(chat_id)

    async def set_activation(self, chat_id: str, activation: Activation) -> None:
        """L2.5: persist the chat's thread activation under the cleanup TTL."""
        await self._activation.set(chat_id, activation)

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

    async def assemble_context(
        self, chat_id: str, budget: int | None = None,
        l3_results: list[dict] | None = None,
        facts: dict[str, str] | None = None,
        messages: list[dict] | None = None,
        identity_prompt: str | None = None,
    ) -> tuple[list[str], int]:
        """Assemble L0-L3 prompt blocks under a dynamic (AITS) budget; returns ``(blocks, l3_used)``.

        L0+L1 mandatory. The AITS ``budget`` is split across L2 and L3 by
        ``allocate_context_budget``: L2 is capped to its share (``≤
        L2_CONTEXT_CAP``, with a floor so it is never fully lost) and L3 gets a
        reserved slice, so L2 can no longer eat the whole budget and starve L3.
        L3 is then fit into the remainder after L0+L1+L2 (silent); ``l3_used`` is
        how many L3 results fit. When ``budget`` is ``None`` it falls back to
        ``MAX_CONTEXT_TOKENS``.

        ``facts`` / ``messages`` let the orchestrator pre-fetch L0/L1 and L2
        concurrently with the prefetch daemon (real overlap); when ``None`` they
        are fetched here (backward-compatible for direct callers/tests).

        L3 results from the daemon carry a ``blended_score`` field (pre-scored by
        ``memory.result_merger``); those are sorted best-first and fit directly,
        skipping the gap filter (the daemon already ranked). Raw Chroma results
        (no ``blended_score``, e.g. from the on-demand ``search_memory`` path or
        direct tests) still go through the gap filter as before.

        Args:
            chat_id: Current chat session ID.
            budget: AITS per-intent token budget (falls back to
                ``MAX_CONTEXT_TOKENS`` when ``None``).
            l3_results: Prefetched episodic results (closest first) or ``None``.
            facts: Pre-fetched L0+L1 facts (``None`` → fetch here).
            messages: Pre-fetched L2 working messages (``None`` → fetch here).
            identity_prompt: Pre-fetched L0 identity text (``None`` → use
                ``_BASE_IDENTITY``). Supplied by the orchestrator after a
                concurrent ``get_identity_prompt()`` call (Task 3).
        """
        if budget is None:
            budget = MAX_CONTEXT_TOKENS
        # L0 + L1: identity + facts — mandatory, never dropped.
        if facts is None:
            facts = await self.get_identity_and_facts()
        now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
        base = identity_prompt if identity_prompt is not None else _BASE_IDENTITY
        identity = f"[Identity]\n{base}\nCurrent time: {now}"
        if facts:
            identity += f"\n\nKnown facts:\n{self._format_facts(facts)}"
        mandatory_tokens = estimate_tokens(identity)
        blocks = [identity]
        # Split the budget: L3 guaranteed minimum first (priority-inverted); L2
        # gets the remainder and so stays AITS-scaled. L3 is never starved to 0.
        l2_cap, l3_guarantee = allocate_context_budget(mandatory_tokens, budget)
        # L2: working context — capped to its share, drop-oldest, never fully lost.
        if messages is None:
            messages = await self.get_working_context(chat_id)
        trimmed = self._trim_recent_messages(messages, l2_cap)
        l2_tokens = 0
        if trimmed:
            l2_block = f"[Conversation History]\n{self._format_messages(trimmed)}"
            l2_tokens = estimate_tokens(l2_block)
            blocks.append(l2_block)
        # L3: episodic — either pre-scored by the daemon (blended_score present,
        # sorted best-first) or raw Chroma results run through the gap filter.
        # Either way the survivors are fit into the budget remaining after
        # L0+L1+L2 (>= l3_guarantee by construction).
        l3_used = 0
        if l3_results:
            l3_budget = max(budget - mandatory_tokens - l2_tokens, 0)
            if l3_budget > 0:
                if any("blended_score" in r for r in l3_results):
                    ordered = sorted(
                        l3_results, key=lambda r: r.get("blended_score", 0.0),
                        reverse=True,
                    )
                    relevant = self._blended_gap_filter(ordered, L3_GAP_SIGNIFICANCE)
                else:
                    relevant = self._gap_filter(l3_results, L3_GAP_SIGNIFICANCE)
                l3_block, l3_used = self._fit_search_results(relevant, l3_budget)
                if l3_block:
                    blocks.append(f"[Context recuperat din istoric]\n{l3_block}")
        return blocks, l3_used

    @staticmethod
    def _trim_recent_messages(messages: list[dict], max_tokens: int) -> list[dict]:
        """Keep the first (topic-setter) + most recent ``messages`` within ``max_tokens``.

        Newest→oldest accumulation, but the very first message is pinned so the
        opening context of the conversation survives a pure recency trim (which
        otherwise loses everything but the tail). The pin only applies when the
        first message is small (< 25% of the cap), so a tight budget is spent on
        recent context rather than an oversized opener. Returns oldest-first.
        Each message estimated as ``role: content``; DEBUG on trim.
        """
        if not messages:
            return []

        def _tok(m: dict) -> int:
            return estimate_tokens(f"{m['role']}: {m['content']}")

        n = len(messages)
        pin_first = n > 1 and _tok(messages[0]) * 4 < max_tokens
        kept_idx: list[int] = []
        total = 0
        if pin_first:
            kept_idx.append(0)
            total += _tok(messages[0])
        for i in range(n - 1, -1, -1):          # newest → oldest
            if i == 0 and pin_first:
                continue                        # already pinned
            tok = _tok(messages[i])
            if total + tok > max_tokens and kept_idx:
                break
            kept_idx.append(i)
            total += tok
        kept_idx.sort()
        kept = [messages[i] for i in kept_idx]
        if len(kept) < n:
            log.debug("L2 trimmed %d->%d messages (cap=%d)", n, len(kept), max_tokens)
        return kept

    @staticmethod
    def _fit_search_results(results: list[dict], max_tokens: int) -> tuple[str, int]:
        """Format results closest-first, keeping as many as fit ``max_tokens``.

        Returns ``(block_text, count)``: joined lines and how many results kept.
        Greedy add-while-fits; silent — partial recall beats a dropped block.
        Each line prefixed with ``[YYYY-MM-DD HH:MM]`` from stored timestamp.
        """
        from datetime import datetime
        lines: list[str] = []
        total = 0
        for r in results:
            ts = r["metadata"].get("timestamp", 0)
            dt = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else ""
            line = f"- [{dt}] {r['content']}" if dt else f"- {r['content']}"
            tok = estimate_tokens(line)
            if total + tok > max_tokens and lines:
                break
            lines.append(line)
            total += tok
        return "\n".join(lines), len(lines)

    @staticmethod
    def _gap_filter(results: list[dict], significance: float = 3.0) -> list[dict]:
        """Keep results before the largest structural gap in the score distribution.

        ChromaDB returns results sorted ascending (closest first). For a genuine
        recall query, relevant docs cluster near the query; a large gap separates
        them from noise. For an unrelated query on a monothematic corpus, all gaps
        are roughly equal — no structural break — so nothing is injected.

        Requires at least 3 results to compute a meaningful ratio (2 gaps). With
        fewer than 3, a generous absolute ceiling (1.5 sq-L2 ≈ cosine 0.25 —
        "nearly orthogonal", from V3 calibration) is applied instead: the ratio
        criterion is meaningless on a single gap, but results beyond 1.5 are
        unambiguously irrelevant regardless of corpus size and should not be
        injected even during the first 1-2 turns of a fresh collection.

        ``significance`` is max_gap / mean_gap; calibrated at 3.0 from 12 labeled
        queries (V3, 2026-06-29): unrelated gap ratios 2.33–2.76 rejected, genuine
        ratios 3.13–5.13 passed. At scale with l2_full_archive docs, archive
        clusters produce ratios >> 10, making this self-calibrating.

        Args:
            results: Score-ascending results from ChromaDB (already sorted).
            significance: max_gap / mean_gap required for a structural break.
        Returns:
            Results before the structural gap, or ``[]`` when none found.
        """
        if not results:
            return []
        if len(results) < 3:
            return [r for r in results if r.get("score", 0.0) < 1.5]
        scores = [r["score"] for r in results]
        gaps = [scores[i + 1] - scores[i] for i in range(len(scores) - 1)]
        max_gap = max(gaps)
        mean_gap = sum(gaps) / len(gaps)
        if mean_gap == 0 or max_gap < significance * mean_gap:
            return []
        cut = gaps.index(max_gap) + 1
        return results[:cut]

    @staticmethod
    def _blended_gap_filter(results: list[dict], significance: float = 3.0) -> list[dict]:
        """Gap filter for pre-scored (blended_score descending) results.

        Applies the same structural-gap principle as ``_gap_filter`` but on
        blended scores rather than raw Chroma distances. Falls back to a
        minimum-score cutoff (``_BLENDED_MIN_SCORE``) when the score distribution
        is uniform (no structural break) — otherwise all mediocre results would
        be injected regardless of quality.
        """
        if not results:
            return []
        if len(results) < 3:
            return [r for r in results if r.get("blended_score", 0.0) >= _BLENDED_MIN_SCORE]
        scores = [r.get("blended_score", 0.0) for r in results]
        gaps = [scores[i] - scores[i + 1] for i in range(len(scores) - 1)]
        max_gap = max(gaps)
        mean_gap = sum(gaps) / len(gaps)
        if mean_gap > 0 and max_gap >= significance * mean_gap:
            cut = gaps.index(max_gap) + 1
            return results[:cut]
        return [r for r in results if r.get("blended_score", 0.0) >= _BLENDED_MIN_SCORE]

    @staticmethod
    def _format_facts(facts: dict[str, str]) -> str:
        """Format L0+L1 facts as ``- key: value`` lines, one per fact."""
        return "\n".join(f"- {key}: {value}" for key, value in facts.items())

    @staticmethod
    def _format_messages(messages: list[dict]) -> str:
        """Format L2 conversation history as ``role: content`` lines, in order."""
        return "\n".join(f"{m['role']}: {m['content']}" for m in messages)