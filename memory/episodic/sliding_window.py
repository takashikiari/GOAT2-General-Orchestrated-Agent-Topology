"""Episodic sliding window — capacity management via pure-LLM relevance scoring.

Episodic memory has NO TTL; this sliding window is what bounds it. When the entry
count reaches the limit, the oldest non-permanent entries are scored by the LLM
and acted on:

  - score < 0.3  → deleted (low value)
  - score >= 0.7 → marked permanent (kept forever, skipped by future windows)
  - 0.3 .. 0.7   → kept (eligible again next time)

Relevance is pure LLM reasoning — no keywords, patterns, regex, or hardcoded
relevance rules. If the LLM is unavailable the window falls back to deleting the
oldest 20 entries so capacity is always enforced. Backends are passed in and must
satisfy the episodic backend Protocol; no singletons, no module state.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from memory.episodic.backend_protocol import EpisodicMemoryBackend

log = logging.getLogger("goat2.memory.episodic.sliding_window")

__all__ = ["check_and_slide"]

MAX_ENTRIES: int = 300
WARN_THRESHOLD: int = 280
_SLIDE_BATCH: int = 100         # oldest entries scored per pass (and fallback delete count)
_FETCH_MAX: int = 400
_DELETE_BELOW: float = 0.3
_PERMANENT_ABOVE: float = 0.7
_CONTEXT_ENTRIES: int = 5
_CONTEXT_CHARS: int = 300

_RELEVANCE_SYSTEM: str = (
    "You decide how relevant one stored memory entry is for keeping in long-term "
    "episodic memory. Given the recent conversation and one entry, judge purely by "
    "meaning whether it carries information likely to matter later (decisions, facts, "
    "results, user preferences) versus transient chatter. Reason from content alone — "
    "no fixed keywords or rules.\n\n"
    "Return ONLY this JSON — no prose:\n"
    '{"relevance_score": 0.0}'
)


def _field(entry, name: str, default=None):
    """Read ``name`` from an entry that may be a dict or a MemoryEntry."""
    if isinstance(entry, dict):
        return entry.get(name, default)
    return getattr(entry, name, default)


def _meta(entry) -> dict:
    """Return the entry's metadata dict (empty dict if missing)."""
    m = _field(entry, "metadata", {}) or {}
    return m if isinstance(m, dict) else {}


def _recent_context(entries: list) -> str:
    """Build a short recent-conversation context from the newest entries."""
    newest = entries[-_CONTEXT_ENTRIES:]
    return "\n".join((_field(e, "content", "") or "")[:_CONTEXT_CHARS] for e in newest)


async def _score_relevance(content: str, context: str) -> float:
    """Score one entry's relevance via the LLM (raises on failure for fallback).

    Uses the supervisor model spec from a locally constructed ``Settings`` (no
    singleton) and the shared LLM helpers. Pure LLM — no keyword/regex rules.
    """
    from config.settings import Settings
    from utils.llm_utils import _call_llm, _extract_json
    spec = Settings().supervisor.model
    raw = await _call_llm(spec, [
        {"role": "system", "content": _RELEVANCE_SYSTEM},
        {"role": "user", "content": f"Recent conversation:\n{context}\n\nEntry:\n{content}\n\nReturn JSON."},
    ])
    data = _extract_json(raw)
    return max(0.0, min(1.0, float(data.get("relevance_score", 1.0))))


async def _fallback_delete(backend: "EpisodicMemoryBackend", agent_role: str, candidates: list) -> None:
    """Delete the oldest ``_SLIDE_BATCH`` non-permanent entries (LLM-unavailable path)."""
    removed = 0
    for entry in candidates[:_SLIDE_BATCH]:
        try:
            await backend.delete(agent_role, _field(entry, "key", ""))
            removed += 1
        except Exception as exc:
            log.debug("fallback delete failed: %s", exc)
    log.info("episodic(%s): fallback deleted oldest %d", agent_role, removed)


async def check_and_slide(
    backend: "EpisodicMemoryBackend", agent_role: str, max_entries: int = MAX_ENTRIES
) -> None:
    """Enforce the episodic capacity bound for ``agent_role`` via LLM relevance.

    WARNs as the count approaches ``max_entries``. At/above the limit, scores the
    oldest non-permanent entries and deletes/keeps/marks-permanent per the bands.
    Falls back to deleting the oldest ``_SLIDE_BATCH`` on any LLM failure. Never raises.
    """
    try:
        count = await backend.count(agent_role)
        if count >= WARN_THRESHOLD:
            log.warning("episodic(%s): approaching limit (%d/%d)", agent_role, count, max_entries)
        if count < max_entries:
            return

        entries = await backend.list(agent_role, limit=_FETCH_MAX)
        candidates = [e for e in entries if not bool(_meta(e).get("permanent", False))]
        candidates.sort(key=lambda e: float(_meta(e).get("created_at_ts") or 0))
        if not candidates:
            log.warning("episodic(%s): over limit but all entries are permanent", agent_role)
            return

        batch = candidates[: max(_SLIDE_BATCH, count - max_entries + 1)]
        context = _recent_context(candidates)
        log.info("episodic(%s): at limit (%d/%d) — scoring %d oldest", agent_role, count, max_entries, len(batch))

        deleted = promoted = kept = 0
        for entry in batch:
            key = _field(entry, "key", "")
            content = _field(entry, "content", "") or ""
            try:
                score = await asyncio.wait_for(
                    _score_relevance(content, context), timeout=10.0
                )
                log.debug("episodic relevance %s: score=%.2f", key, score)
            except asyncio.TimeoutError:
                log.error("episodic(%s): relevance LLM timed out — fallback delete oldest %d", agent_role, _SLIDE_BATCH)
                return await _fallback_delete(backend, agent_role, candidates)
            except Exception as exc:
                log.error("episodic(%s): relevance LLM failed — fallback: %s", agent_role, exc)
                return await _fallback_delete(backend, agent_role, candidates)
            try:
                if score < _DELETE_BELOW:
                    await backend.delete(agent_role, key)
                    deleted += 1
                elif score >= _PERMANENT_ABOVE:
                    meta = {"permanent": True, "compartment": _meta(entry).get("compartment", "")}
                    await backend.store(agent_role, key, content, metadata=meta)
                    promoted += 1
                else:
                    kept += 1
            except Exception as exc:
                log.debug("episodic slide op failed for %s: %s", key, exc)
        log.info("episodic(%s): deleted=%d permanent=%d kept=%d", agent_role, deleted, promoted, kept)
    except Exception as exc:
        log.debug("check_and_slide failed: %s", exc)
