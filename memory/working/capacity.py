"""Working memory capacity management — max 100 entries, LLM-scored promotion.

When working memory is full, the oldest non-``dag:`` entries are candidates for
promotion to the episodic tier. Rather than promoting blindly, an LLM scores each
candidate against the recent conversation and decides whether it is worth keeping:
relevant entries are promoted to episodic, irrelevant ones are dropped. This keeps
episodic memory clean. If the LLM is unavailable, the batch falls back to
promote-all (the prior behavior) so capacity is always enforced.

The recent-conversation context is built from the newest working entries
themselves, so this module stays self-contained — it never imports ``supervisor``.
Relevance is pure LLM reasoning: no hardcoded keywords, patterns, or regex.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from memory.working.backend_protocol import WorkingMemoryBackend

log = logging.getLogger("goat2.memory.working.capacity")

MAX_ENTRIES: int = 100
WARN_THRESHOLD: int = 90

# How many of the newest entries form the "recent conversation" context, and the
# per-entry char cap applied when building that context for the LLM.
_CONTEXT_ENTRIES: int = 5
_CONTEXT_CHARS: int = 300

_RELEVANCE_SYSTEM: str = (
    "You decide whether a single working-memory entry is worth keeping in long-term "
    "episodic memory. You are given the recent conversation and one entry. Judge, purely "
    "by meaning, whether the entry carries information likely to matter later (decisions, "
    "facts, results, user preferences) versus transient chatter. Reason from the content "
    "alone — no fixed keywords or rules.\n\n"
    "Return ONLY this JSON — no prose:\n"
    '{"relevance_score": 0.0, "promote": true}'
)


async def get_promotable_entries(backend: "WorkingMemoryBackend", agent_role: str) -> list[dict]:
    """Return all non-dag entries for ``agent_role`` sorted oldest first."""
    try:
        keys = await backend.keys(agent_role)
        entries: list[dict] = []
        for key in keys:
            if "dag:" in str(key):
                continue
            record = await backend.get(agent_role, key)
            if record:
                entries.append(record)
        entries.sort(key=lambda e: e.get("created_at_ts", 0))
        log.debug("get_promotable_entries(%s): %d promotable (dag:* excluded)", agent_role, len(entries))
        return entries
    except Exception as exc:
        log.debug("get_promotable_entries failed: %s", exc)
        return []


def _recent_context(promotable: list[dict]) -> str:
    """Build a short recent-conversation context from the newest entries."""
    newest = promotable[-_CONTEXT_ENTRIES:]
    return "\n".join((e.get("content", "") or "")[:_CONTEXT_CHARS] for e in newest)


async def _score_relevance(content: str, context: str) -> tuple[float, bool]:
    """Score one entry's relevance via the LLM; returns (relevance_score, promote).

    Raises on any failure so the caller can fall back to promote-all. Uses the
    supervisor model spec from Settings (constructed locally — no singleton) and
    the shared LLM helpers. Pure LLM reasoning, no keyword/regex rules.
    """
    from config.settings import Settings
    from utils.llm_utils import _call_llm, _extract_json
    spec = Settings().supervisor.model
    raw = await _call_llm(spec, [
        {"role": "system", "content": _RELEVANCE_SYSTEM},
        {"role": "user", "content": f"Recent conversation:\n{context}\n\nEntry:\n{content}\n\nReturn JSON."},
    ])
    data = _extract_json(raw)
    score = max(0.0, min(1.0, float(data.get("relevance_score", 1.0))))
    return score, bool(data.get("promote", True))


async def check_and_promote(
    working_backend: "WorkingMemoryBackend",
    episodic_backend,
    agent_role: str,
    max_entries: int = MAX_ENTRIES,
) -> int:
    """Enforce capacity: LLM-score the oldest entries, promote the relevant, drop the rest.

    WARNs as the count approaches ``max_entries``. At the limit, the oldest
    promotable entries are each scored by the LLM: ``promote=true`` → written to
    episodic and removed from working; ``promote=false`` → removed only (dropped).
    If any LLM score fails, the whole batch falls back to promote-all so capacity
    is still enforced. ``dag:`` entries are never promoted.

    Returns:
        Number of entries successfully promoted to episodic (0 when the
        function short-circuits or all writes fail).
    """
    try:
        count = len(await working_backend.keys(agent_role))
        if count >= WARN_THRESHOLD:
            log.warning("capacity(%s): approaching limit (%d/%d)", agent_role, count, max_entries)
        if count < max_entries:
            log.debug("capacity(%s): under limit (%d/%d) — no promotion", agent_role, count, max_entries)
            return 0
        log.info("capacity(%s): at limit (%d/%d) — scoring oldest for promotion", agent_role, count, max_entries)

        promotable = await get_promotable_entries(working_backend, agent_role)
        to_promote = promotable[: max(1, count - max_entries + 1)]
        context = _recent_context(promotable)

        # Score each candidate; on any LLM failure, fall back to promote-all.
        decisions: dict[str, bool] = {}
        llm_ok = True
        for entry in to_promote:
            try:
                score, promote = await _score_relevance(entry.get("content", ""), context)
                decisions[entry.get("key", "")] = promote
                log.debug("relevance(%s): score=%.2f promote=%s", entry.get("key"), score, promote)
            except Exception as exc:
                log.warning("capacity(%s): relevance LLM failed — promote-all fallback: %s", agent_role, exc)
                llm_ok = False
                break

        promoted = dropped = 0
        for entry in to_promote:
            key = entry.get("key", "")
            content = entry.get("content", "")
            promote = True if not llm_ok else decisions.get(key, True)
            try:
                if promote and episodic_backend and content:
                    await episodic_backend.store(agent_role, key, content, metadata=entry.get("metadata") or None)
                    promoted += 1
                elif not promote:
                    dropped += 1
                await working_backend.delete(agent_role, key)
            except Exception as exc:
                log.debug("promote/drop entry failed: %s", exc)
        remaining = max(0, count - promoted - dropped)
        log.info("capacity(%s): promoted %d, dropped %d (llm=%s, remaining ~%d entries)",
                 agent_role, promoted, dropped, llm_ok, remaining)
        return promoted
    except Exception as exc:
        log.debug("check_and_promote failed: %s", exc)
        return 0
