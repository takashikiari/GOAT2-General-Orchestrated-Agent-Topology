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
    PREFETCH_RECENCY_BASE_WEIGHT,
    PREFETCH_RECENCY_RECENCY_WEIGHT,
    PREFETCH_RECENCY_WINDOW_DAYS,
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
    "update_centroid_weighted",
    "find_topic_return",
    "archive_current_topic",
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
        ts: the writing turn's ORIGIN wall clock (housekeeping display, plus —
            since 2026-07-12 — the monotonic ordering key ``ActivationStore.set``
            compare-and-sets on to reject out-of-order writes from a slower
            prefetch; never a reset trigger regardless).
        topic_id: UUID string for the current conversation topic; assigned on cold
            break, empty string for blobs written before this field existed.
        turn_count: number of turns in the current topic; drives centroid alpha.
        archived_topics: up to ``TOPIC_ARCHIVE_MAX`` past topic centroids, each a
            dict with ``topic_id`` and ``centroid`` keys (newest last).
    """

    centroid: list[float] = field(default_factory=list)
    merged: list[dict] = field(default_factory=list)
    last_query: str = ""
    recent_queries: list[str] = field(default_factory=list)
    ts: float = 0.0
    topic_id: str = ""
    turn_count: int = 0
    archived_topics: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialize to a JSON-friendly dict for Redis storage."""
        return {
            "centroid": self.centroid,
            "merged": self.merged,
            "last_query": self.last_query,
            "recent_queries": self.recent_queries,
            "ts": self.ts,
            "topic_id": self.topic_id,
            "turn_count": self.turn_count,
            "archived_topics": self.archived_topics,
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
            topic_id=str(data.get("topic_id") or ""),
            turn_count=int(data.get("turn_count") or 0),
            archived_topics=list(data.get("archived_topics") or []),
        )


# Atomic compare-and-set for ActivationStore.set (2026-07-12 write-race fix).
# The prefetch daemon (orchestrator/prefetch.py) is fire-and-forget with no
# timeout by design ("has as long as it needs before the user's next
# message") — so turn N's write can finish AFTER turn N+1's already-faster
# write. A plain SET/SETEX would let turn N silently clobber turn N+1's
# fresher activation with stale data (topic_id, centroid, merged L3 results,
# turn_count); turn N+2 then warm-serves from that wrong blob.
#
# Guard: reject the incoming write if the CURRENTLY-STORED blob's ``ts``
# (the turn's ORIGIN timestamp — see update_activation's ``turn_start``, NOT
# this write's own completion time) is strictly newer than the incoming
# write's ``ts``. Runs as a single EVAL — Redis executes Lua scripts
# atomically (no other command interleaves), so this is a true compare-and-
# set with no read-then-write race window of its own, unlike a Python-side
# GET followed by a separate SET. A corrupt/undecodable stored blob is
# treated the same as "nothing stored" (never blocks a write), matching
# ``get()``'s corrupt-is-miss behaviour.
_CAS_SET_SCRIPT = """
local current = redis.call('GET', KEYS[1])
local new_ts = tonumber(ARGV[2])
if current then
    local ok, decoded = pcall(cjson.decode, current)
    if ok and type(decoded) == 'table' and decoded.ts ~= nil then
        local stored_ts = tonumber(decoded.ts)
        if stored_ts ~= nil and stored_ts > new_ts then
            return 0
        end
    end
end
local ttl = tonumber(ARGV[3])
if ttl > 0 then
    redis.call('SETEX', KEYS[1], ttl, ARGV[1])
else
    redis.call('SET', KEYS[1], ARGV[1])
end
return 1
"""


class ActivationStore:
    """Redis-backed per-chat activation store (one blob per chat).

    Reuses the working-memory Redis client (shared pool) and the long cleanup
    TTL. ``get`` returns ``None`` on absence/expiry/corrupt JSON (never raises);
    ``set`` atomically compare-and-sets a JSON blob under ``activation:{chat_id}``
    — see ``_CAS_SET_SCRIPT``.
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

    async def set(self, chat_id: str, activation: Activation) -> bool:
        """Compare-and-set ``activation`` under the cleanup TTL.

        Atomically (single Redis EVAL, see ``_CAS_SET_SCRIPT``) rejects the
        write if a currently-stored activation has a strictly newer ``ts``
        than the one being written — an older (turn-origin-wise) write can
        never clobber a newer one, even if it finishes later. Returns
        ``True`` if the write was applied, ``False`` if rejected as stale.
        """
        payload = json.dumps(activation.to_dict())
        client = self._working._get_client()
        applied = await client.eval(
            _CAS_SET_SCRIPT, 1, self._key(chat_id), payload, str(activation.ts), str(self._ttl),
        )
        if applied:
            log.debug("ActivationStore SET chat=%s ttl=%ss", chat_id, self._ttl)
        else:
            log.info(
                "ActivationStore SET REJECTED (stale write) chat=%s ts=%s",
                chat_id, activation.ts,
            )
        return bool(applied)

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


# Romanian function-word stoplist for the lexical-overlap Jaccard signal only
# (never used for embedding/search — this is purely to stop short follow-ups'
# few content words from being swamped by "de/și/pe/nu/am" noise). Curated,
# not exhaustive: common prepositions, conjunctions, pronouns, and the verb
# "a fi"/"a avea" auxiliaries, each with and without diacritics since real
# chat text is inconsistent about them. Deliberately excludes anything that
# could carry topic meaning (no nouns/verbs/adjectives, no question words
# like "ce"/"cine"/"unde" — those anchor a topic and must stay eligible for
# overlap).
_STOPWORDS_RO: frozenset[str] = frozenset({
    "a", "ai", "al", "ale", "am", "ar", "are", "asta", "astea", "atunci",
    "au", "acum", "aici", "acolo",
    "ca", "care", "cu",
    "da", "dar", "deci", "din", "de",
    "e", "este", "esti", "ești", "eu",
    "i", "ii", "îi", "il", "îl", "in", "în", "isi", "își",
    "la", "le", "lui",
    "ma", "mă", "mai", "mea", "meu", "mi", "mie",
    "ne", "nu", "noi", "noastra", "noastră",
    "o", "or", "ori",
    "pe", "prin", "pai", "păi",
    "sa", "să", "sau", "se", "si", "și", "sunt", "sunteti", "sunteți",
    "ta", "te", "ti", "ți", "tu",
    "un", "una", "unei", "unui",
    "va", "vă", "voi",
})


def _tokens(text: str) -> set[str]:
    """Lowercased, punctuation-stripped, stopword-filtered word tokens.

    Stopword filtering (2026-07-12) is applied HERE ONLY — it never touches
    embeddings/search, just the Jaccard lexical-overlap signal. Without it,
    short Romanian follow-ups are dominated by function words ("de", "și",
    "azi", "pe", "am", "nu"), which inflates apparent lexical DIVERGENCE
    between genuinely-related short messages (their few content words don't
    overlap much even when the topic is identical) rather than helping
    detect continuity. See ``lexical_overlap`` / ``classify_turn``.
    """
    raw = {tok.strip(string.punctuation).lower() for tok in (text or "").split()} - {""}
    return raw - _STOPWORDS_RO


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
    """Re-weight held results by recency relative to the current time.

    CrossEncoder sigmoid scores (0-1) stored in ``blended_score`` are the
    primary relevance signal. Recency adjusts the final order:
    ``PREFETCH_RECENCY_BASE_WEIGHT`` base score + ``PREFETCH_RECENCY_RECENCY_WEIGHT``
    recency fraction over the configured window (config/memory.toml
    [prefetch], default 70/30). A result with high CrossEncoder score but
    growing age drops gradually — "attenuates, never resets". Returns a
    fresh, best-first list.
    """
    window = PREFETCH_RECENCY_WINDOW_DAYS * 86400
    out: list[dict] = []
    for r in merged:
        r2 = dict(r)
        ts = float((r.get("metadata") or {}).get("timestamp", 0) or 0)
        recency = max(0.0, 1.0 - (now - ts) / window)
        base = r.get("blended_score", 0.5)
        r2["blended_score"] = (
            PREFETCH_RECENCY_BASE_WEIGHT * base + PREFETCH_RECENCY_RECENCY_WEIGHT * recency
        )
        out.append(r2)
    out.sort(key=lambda r: r.get("blended_score", 0.0), reverse=True)
    return out


def trim_recent(recent: list[str], query: str) -> list[str]:
    """Append ``query`` to the recent-queries window, capped at the configured size."""
    out = list(recent) + [query]
    if len(out) > ACTIVATION_LEXICAL_WINDOW:
        out = out[-ACTIVATION_LEXICAL_WINDOW:]
    return out


def update_centroid_weighted(
    centroid: list[float], query_emb: list[float], turn_count: int,
) -> list[float]:
    """Stability-weighted centroid update.

    Early turns (turn_count small) allow large moves; a stable topic
    (turn_count → 20) resists drift with alpha → 0.05. Caps at turn 20
    so the minimum alpha is 5% (centroid can always track the thread).
    """
    alpha = 1.0 / min(max(turn_count, 1), 20)
    return [(1.0 - alpha) * c + alpha * q for c, q in zip(centroid, query_emb)]


def find_topic_return(
    query_emb: list[float] | None,
    archived_topics: list[dict],
    threshold: float,
) -> str | None:
    """Return the archived ``topic_id`` whose centroid best matches ``query_emb``.

    Compares the new query embedding against every archived topic centroid via
    cosine similarity. Returns the best-matching ``topic_id`` when the best
    similarity meets ``threshold``, else ``None``. None-safe on both inputs.
    """
    if not query_emb or not archived_topics:
        return None
    best_sim, best_id = 0.0, None
    for entry in archived_topics:
        sim = cosine(query_emb, entry.get("centroid") or [])
        if sim > best_sim:
            best_sim, best_id = sim, entry.get("topic_id")
    return best_id if best_id and best_sim >= threshold else None


def archive_current_topic(activation: "Activation", max_archived: int) -> list[dict]:
    """Snapshot the current topic centroid into the archived list.

    Removes any prior entry with the same ``topic_id`` (dedup on re-visit),
    appends the current centroid as a new snapshot, then trims to
    ``max_archived`` (newest-last). Returns a new list; ``activation`` is
    not mutated.
    """
    if not activation.centroid or not activation.topic_id:
        return list(activation.archived_topics)
    entry = {"topic_id": activation.topic_id, "centroid": activation.centroid, "ts": activation.ts}
    deduped = [a for a in activation.archived_topics if a.get("topic_id") != activation.topic_id]
    deduped.append(entry)
    return deduped[-max_archived:]