"""memory.activation — L2.5 as a brain activation layer.

Per-chat thread state that holds the recently-activated retrieval steady along
a topic thread, so the LLM builds on a stable reality instead of re-grounding
from a jittering L3 slice every turn. The activation **persists by default**;
a thread breaks only on a CONSENSUS shift (embedding drift AND lexical overlap
both drop); an on-thread (enriching) write refreshes the activation in place,
synchronously before the next turn reads it. Time never resets it — only
attenuates, via the recency term re-scored on warm serve (``rescore_recency``).

Storage is a single JSON blob per chat under ``activation:{chat_id}`` (Redis,
reusing the working-memory client), with a long cleanup TTL — **not a reset**:
expiry just means "re-derive cold next time"; topic continuity is semantic.

Pure functions (``cosine``, ``lexical_overlap``, ``classify_turn``,
``classify_write``, ``rescore_recency``) take plain values and are unit-testable
with no services. The store is the only I/O surface.
"""
from __future__ import annotations

import json
import math
import string
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from memory.config import (
    ACTIVATION_DRIFT_COLD,
    ACTIVATION_DRIFT_WARM,
    ACTIVATION_ENRICHING_SIM,
    ACTIVATION_LEXICAL_LOW,
    ACTIVATION_LEXICAL_WINDOW,
    ACTIVATION_TTL_SECONDS,
)
from utils.logging.setup import get_logger

if TYPE_CHECKING:
    from memory.working import WorkingMemory

log = get_logger(__name__)

# Redis key namespace — distinct from SessionCache's ``cache:`` and working
# memory's ``goat2:working:`` so the activation never collides with either.
_ACTIVATION_PREFIX = "activation"

__all__ = [
    "Activation",
    "ActivationStore",
    "cosine",
    "lexical_overlap",
    "classify_turn",
    "classify_write",
    "rescore_recency",
    "trim_recent",
]


@dataclass
class Activation:
    """Per-chat thread state held in L2.5.

    Attributes:
        centroid: the thread's query embedding (set on cold/drift turns, held
            steady on warm turns so short follow-ups can't move it).
        merged: the cold turn's final merged+scored L3 results; served (re-scored
            by recency) on warm turns.
        last_query: the substantive query that produced ``merged``; the
            enriching-write refresh re-searches against this.
        recent_queries: rolling window of recent queries for the lexical-overlap
            consensus signal (newest last, capped at ``ACTIVATION_LEXICAL_WINDOW``).
        ts: last-write wall clock (housekeeping; never a reset trigger).
    """

    centroid: list[float] = field(default_factory=list)
    merged: list[dict] = field(default_factory=list)
    last_query: str = ""
    recent_queries: list[str] = field(default_factory=list)
    ts: float = 0.0

    def to_dict(self) -> dict:
        """Serialize to a JSON-friendly dict for Redis storage."""
        return {
            "centroid": self.centroid,
            "merged": self.merged,
            "last_query": self.last_query,
            "recent_queries": self.recent_queries,
            "ts": self.ts,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Activation":
        """Reconstruct from a stored dict; missing fields default safely."""
        return cls(
            centroid=list(data.get("centroid") or []),
            merged=list(data.get("merged") or []),
            last_query=str(data.get("last_query") or ""),
            recent_queries=list(data.get("recent_queries") or []),
            ts=float(data.get("ts") or 0.0),
        )


class ActivationStore:
    """Redis-backed per-chat activation store (one blob per chat).

    Reuses the working-memory Redis client (shared pool) and the long cleanup
    TTL. ``get`` returns ``None`` on absence/expiry/corrupt JSON (never raises);
    ``set`` writes a JSON blob under ``activation:{chat_id}``.
    """

    def __init__(self, working_memory: "WorkingMemory", ttl_seconds: int = ACTIVATION_TTL_SECONDS) -> None:
        self._working = working_memory
        self._ttl = ttl_seconds

    def _key(self, chat_id: str) -> str:
        """Build the Redis key: ``activation:{chat_id}``."""
        return f"{_ACTIVATION_PREFIX}:{chat_id}"

    async def get(self, chat_id: str) -> Activation | None:
        """Retrieve the chat's activation, or ``None`` if absent/expired/corrupt."""
        data = await self._working._get_client().get(self._key(chat_id))
        if data is None:
            log.debug("ActivationStore MISS chat=%s", chat_id)
            return None
        try:
            value = json.loads(data)
        except json.JSONDecodeError:
            log.warning("ActivationStore corrupt at %s, treating as miss", chat_id)
            return None
        if not isinstance(value, dict):
            log.warning("ActivationStore non-dict at %s, treating as miss", chat_id)
            return None
        log.debug("ActivationStore HIT chat=%s", chat_id)
        return Activation.from_dict(value)

    async def set(self, chat_id: str, activation: Activation) -> None:
        """Store ``activation`` under the cleanup TTL (persisting without a reset)."""
        payload = json.dumps(activation.to_dict())
        client = self._working._get_client()
        if self._ttl > 0:
            await client.setex(self._key(chat_id), self._ttl, payload)
        else:
            await client.set(self._key(chat_id), payload)
        log.debug("ActivationStore SET chat=%s ttl=%ss", chat_id, self._ttl)

    async def clear(self, chat_id: str) -> None:
        """Drop the chat's activation (no-op if absent). Logs the removal count."""
        removed = await self._working._get_client().delete(self._key(chat_id))
        log.debug("ActivationStore CLEAR chat=%s removed=%s", chat_id, removed)


# --- pure logic ---------------------------------------------------------------

def cosine(a: list[float] | None, b: list[float] | None) -> float:
    """Cosine similarity of two vectors; ``0.0`` if either is None/empty.

    None-safe so callers can pass embeddings that degraded to ``None`` (which
    forces a cold turn via ``classify_turn``) without guarding everywhere.
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _tokens(text: str) -> set[str]:
    """Lowercased, punctuation-stripped word tokens (matches the aits idiom)."""
    return {tok.strip(string.punctuation).lower() for tok in (text or "").split()} - {""}


def lexical_overlap(query: str, recent_queries: list[str]) -> float:
    """Jaccard overlap between ``query`` tokens and the union of recent queries.

    Catches entity/topic changes the embedding may miss (e.g. a new named
    entity drops lexical overlap even when the surface form is similar). Returns
    ``0.0`` when either side has no tokens.
    """
    a = _tokens(query)
    if not a or not recent_queries:
        return 0.0
    b: set[str] = set()
    for q in recent_queries:
        b |= _tokens(q)
    if not b:
        return 0.0
    return len(a & b) / len(a | b)


def classify_turn(query: str, activation: Activation | None, query_emb: list[float] | None) -> str:
    """Classify a turn as ``"cold"``, ``"warm"``, or ``"drift"``.

    * ``cold`` — no prior activation, no embedding (degraded), or a CONSENSUS
      shift: cosine(query, centroid) < ``ACTIVATION_DRIFT_COLD`` AND
      lexical_overlap < ``ACTIVATION_LEXICAL_LOW``. Both must drop — a single
      jittery signal (e.g. a short "tell me more" drifts in embedding space but
      keeps lexical overlap) must NOT reset the thread.
    * ``warm`` — cosine ≥ ``ACTIVATION_DRIFT_WARM``: same thread, serve the held
      activation and skip all three prefetch mechanisms.
    * ``drift`` — the middle band: the query moved but not enough to be a shift;
      a targeted single-mechanism refresh re-runs thematic against the new query.
    """
    if activation is None or query_emb is None or not activation.centroid:
        return "cold"
    drift = cosine(query_emb, activation.centroid)
    if drift < ACTIVATION_DRIFT_COLD:
        lex = lexical_overlap(query, activation.recent_queries)
        if lex < ACTIVATION_LEXICAL_LOW:
            return "cold"  # consensus shift — both signals dropped
    if drift >= ACTIVATION_DRIFT_WARM:
        return "warm"
    return "drift"


def classify_write(content_emb: list[float] | None, centroid: list[float] | None) -> str:
    """Classify a write as ``"enriching"`` (on-thread) or ``"filing"`` (off-thread).

    ``"enriching"`` when cosine(content, centroid) ≥ ``ACTIVATION_ENRICHING_SIM`` —
    the written content belongs to the current thread, so the activation is
    refreshed in place. ``"filing"`` otherwise (or when there is no activation /
    embedding) — the content is stored in L3 and surfaces when a future thread
    about that topic activates; the current activation is left untouched.
    """
    if not content_emb or not centroid:
        return "filing"
    return "enriching" if cosine(content_emb, centroid) >= ACTIVATION_ENRICHING_SIM else "filing"


def rescore_recency(merged: list[dict], now: float) -> list[dict]:
    """Re-score the held results' blended score with the current time.

    The similarity and access terms are time-invariant within a thread; only the
    recency term changes as ``now`` advances. Re-blending with the current ``now``
    makes older held results drop in score across a long thread — the "time
    attenuates, never resets" property — with no separate attenuation code and
    no search. Reuses ``result_merger._blended`` (same package) so the blend
    weights stay in one place. Returns a fresh, best-first list.
    """
    from memory.result_merger import _blended

    out: list[dict] = []
    for r in merged:
        r2 = dict(r)
        r2["blended_score"] = _blended(r, now)
        out.append(r2)
    out.sort(key=lambda r: r.get("blended_score", 0.0), reverse=True)
    return out


def trim_recent(recent: list[str], query: str) -> list[str]:
    """Append ``query`` to the recent-queries window, capped at the configured size."""
    out = list(recent) + [query]
    if len(out) > ACTIVATION_LEXICAL_WINDOW:
        out = out[-ACTIVATION_LEXICAL_WINDOW:]
    return out