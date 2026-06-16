"""Extract key-value facts from user messages via pattern matching — pure middleware.

NO LLM. Three pattern families: key-value (``X is Y``, ``X = Y``),
preference (``I prefer X``, ``vreau X``), and named-entity
(capitalized multi-word sequences). Same external signature as
the old LLM-based version so call sites are unchanged. Routing
to memory (Letta / ChromaDB) is preserved.
"""
from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING, Final

from config.roles import GOAT_ROLE

if TYPE_CHECKING:
    from memory.shared import MemoryManager
    from config.registry import Registry

from memory.shared.memory_enums import MemoryType
from memory.shared.pollution_guard import PollutionGuard
from memory.shared.types import MemoryEntryMetadata
from supervisor.behavior.info_types import INFERRED_TTL, ScoredFact

__all__ = ["maybe_store_info"]

_KEY:  Final[str] = "human"
_GUARD = PollutionGuard()

# Same whitelist as the old LLM-based version. Keyed by lowercased name.
_ALLOWED_KEYS: Final[frozenset[str]] = frozenset({
    "name", "age", "location", "city", "language", "workspace",
    "gender", "occupation", "preferences", "rules", "canal",
    "device", "nationality",
})

_BLOCKED: Final[frozenset[str]] = frozenset({
    "agent_id", "passage_id", "search_key", "limit", "offset",
    "score", "source", "memory_type", "ttl", "count",
    "timestamp", "created_at", "updated_at",
})

# Key-value patterns. Each tuple is (compiled_regex, key_extractor).
# Groups: (1) the key, (2) the value. The key_extractor turns the
# raw match into a canonical (key, value) pair. Patterns are
# intentionally tight — we want high precision, not recall; the
# GOAT model can use its memory tools for anything we miss.
_KV_PATTERNS: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    # "my name is X" / "name is X" / "numele meu este X" — stop at
    # the next sentence boundary or conjunction.
    (re.compile(
        r"\b(?:my\s+name\s+is|name\s+is|my\s+name's|i'?m\s+called|"
        r"numele\s+meu\s+este|ma\s+numesc|mă\s+numesc)\s+"
        r"([A-ZĂÂÎȘȚ][A-Za-zăâîșțĂÂÎȘȚ\-\s]{1,30}?)(?=[\.,;\!\?\n]|$|\s+(?:and|și|si|dar|dar)\b)",
        re.IGNORECASE,
    ), "name"),
    # "I am X years old" / "am X ani"
    (re.compile(
        r"\b(?:i\s+am|am)\s+(\d{1,3})\s+(?:ani|years?\s+old)\b",
        re.IGNORECASE,
    ), "age"),
    # "I am from X" / "is from X" / "are from X" / "sunt din X" /
    # "locuiesc în X" — the subject can be "I", "he", "she", "we",
    # "they" or a proper name. The verb is "am"/"is"/"are" or the
    # Romanian equivalents. Case-insensitive so "She" / "she" both
    # work.
    (re.compile(
        r"\b(?:i\s+am\s+from|i'm\s+from|i\s+am\s+from|"
        r"(?:he|she|we|they|he|she|it)\s+(?:is|are)\s+from|"
        r"(?:he|she|we|they)'s\s+from|"
        r"sunt\s+din|suntem\s+din|"
        r"locuiesc\s+în|locuiesc\s+in)\s+"
        r"([A-ZĂÂÎȘȚ][A-Za-zăâîșțĂÂÎȘȚ\-\s]{1,30}?)(?=[\.,;\!\?\n]|$|\s+(?:și|si|and|dar)\b)",
        re.IGNORECASE,
    ), "location"),
    # "I work at X" / "lucrez la X"
    (re.compile(
        r"\b(?:lucrez\s+(?:la|în|in)|work\s+(?:at|in|for))\s+"
        r"([A-ZĂÂÎȘȚ][A-Za-zăâîșțĂÂÎȘȚ\-\s]{1,30}?)(?=[\.,;\!\?\n]|$|\s+(?:și|si|and|dar)\b)",
    ), "occupation"),
    # "I speak X" / "vorbesc X"
    (re.compile(
        r"\b(?:i\s+speak|i\s+know|vorbesc|știu|stiu)\s+"
        r"(român[ăa]|romanian|engleză|english|franceză|french|germană|german|"
        r"spaniolă|spanish|italiană|italian)\b",
        re.IGNORECASE,
    ), "language"),
    # "I prefer X" / "I like X" / "vreau X" / "imi place X" — stop
    # at sentence boundary.
    (re.compile(
        r"\b(?:i\s+prefer|i\s+like|i\s+love|i\s+want|"
        r"prefer|vreau|aș\s+vrea|as\s+vra|îmi\s+place|imi\s+place)\s+"
        r"([A-Za-zăâîșțĂÂÎȘȚ][A-Za-zăâîșțĂÂÎȘȚ\-\s]{1,60}?)(?=[\.,;\!\?\n]|$|\s+(?:și|si|and|dar)\b)",
        re.IGNORECASE,
    ), "preferences"),
    # Generic "X is Y" / "X = Y" / "X: Y" — value-extraction heuristic.
    # The first group must be a short key-shaped word. Stop at
    # sentence boundaries OR conjunctions to avoid greedy capture
    # across clauses. The ``e`` keyword is anchored to a word
    # boundary on both sides so it does not fire on the ``e`` at
    # the end of ``place``/``name``/etc.
    (re.compile(
        r"\b([a-zA-Z_][a-zA-Z0-9_]{1,30})\s*(?:is|=|:|\beste\b|\be\b)\s+"
        r"([A-Za-zăâîșțĂÂÎȘȚ0-9][A-Za-zăâîșțĂÂÎȘȚ0-9\-\s\.]{0,80}?)"
        r"(?=[\.,;\!\?\n]|$|\s+(?:and|or|but|și|si|dar|so)\b)",
    ), "generic"),
)

# Heuristic: a key that looks like a fact (lowercased identifier).
_GENERIC_STOPKEYS: Final[frozenset[str]] = frozenset({
    "it", "this", "that", "there", "here", "what", "which", "who",
    "when", "where", "how", "why", "i", "you", "we", "they", "he",
    "she", "the", "a", "an", "and", "or", "but", "if", "so", "to",
})

# Named-entity pattern: capitalized multi-word sequences (proper
# nouns) introduced by an explicit "is"/"am"/"sunt"/"name"/
# "este"/"e"/"nume" prefix. This avoids matching the first
# word of every sentence (which is capitalized in Romanian
# and English alike) and stops at the next sentence boundary.
_NAMED_ENTITY_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:is|am|sunt|este|e|name|numele)\s+"
    r"([A-ZĂÂÎȘȚ][a-zăâîșț]+"
    r"(?:\s+[A-ZĂÂÎȘȚ][a-zăâîșț]+){0,2})"
    r"(?=[\.,;\!\?\n]|$|\s+(?:și|si|and|dar)\b)",
)


def _clean(value: str) -> str:
    """Trim trailing punctuation and whitespace from a captured value."""
    return value.strip().rstrip(".,;:!?\n\t ")


def _extract_kv(message: str) -> list[ScoredFact]:
    """Walk the pattern table; return every (key, value) hit as an explicit fact."""
    facts: list[ScoredFact] = []
    for pattern, key_name in _KV_PATTERNS:
        for m in pattern.finditer(message):
            if key_name == "generic":
                key = m.group(1).strip().lower()
                value = _clean(m.group(2))
                if key in _GENERIC_STOPKEYS or key in _BLOCKED or key.endswith("_id"):
                    continue
                if not value or len(value) > 80:
                    continue
                facts.append({"key": key, "value": value, "kind": "explicit"})
            else:
                value = _clean(m.group(1))
                if not value or len(value) > 80:
                    continue
                facts.append({"key": key_name, "value": value, "kind": "explicit"})
    return facts


def _extract_named_entities(message: str) -> list[ScoredFact]:
    """Find capitalized multi-word sequences and store them as ``name`` facts."""
    facts: list[ScoredFact] = []
    seen: set[str] = set()
    for m in _NAMED_ENTITY_RE.finditer(message):
        value = _clean(m.group(1))
        if not value or len(value) < 3:
            continue
        if value.lower() in seen:
            continue
        seen.add(value.lower())
        facts.append({"key": "name", "value": value, "kind": "explicit"})
    return facts


def _merge(existing: str, new_pairs: dict[str, str]) -> str:
    """Overlay new_pairs onto existing 'key: value' block; skips blocked keys. Pure — PyO3 candidate."""
    index: dict[str, str] = {}
    for line in existing.splitlines():
        if ":" in line:
            k = line.partition(":")[0].strip().lower()
            if k in _ALLOWED_KEYS and k not in _BLOCKED and not k.endswith("_id"):
                index[k] = line
    for k, v in new_pairs.items():
        nk = k.strip().lower()
        if nk in _ALLOWED_KEYS and nk not in _BLOCKED and not nk.endswith("_id"):
            index[nk] = f"{k.strip()}: {v.strip()}"
    return "\n".join(index.values())


async def _store_in_chroma(mm: MemoryManager, facts: list[ScoredFact]) -> None:
    """Persist non-whitelisted explicit facts to ChromaDB with 7-day TTL."""
    exp = time.time() + INFERRED_TTL
    for f in facts:
        nk = f["key"].strip().lower()
        if nk not in _ALLOWED_KEYS:
            continue
        if nk in _BLOCKED or nk.endswith("_id"):
            continue
        meta = MemoryEntryMetadata(tags=["info_extract"], expires_at_ts=exp)
        await mm.store(
            GOAT_ROLE, f"info:{nk}", f"{nk}: {f['value'].strip()}",
            memory_type=MemoryType.EPISODIC, metadata=meta,
        )


async def _store_inferred(mm: MemoryManager, facts: list[ScoredFact]) -> None:
    """Persist inferred facts to ChromaDB tagged 'inferred' with expires_at_ts = now + 7 days."""
    exp = time.time() + INFERRED_TTL
    for f in facts:
        nk = f["key"].strip().lower()
        if nk in _BLOCKED or nk.endswith("_id"):
            continue
        meta = MemoryEntryMetadata(tags=["inferred"], expires_at_ts=exp)
        await mm.store(
            GOAT_ROLE, f"inferred:{nk}", f"{nk}: {f['value'].strip()}",
            memory_type=MemoryType.EPISODIC, metadata=meta,
        )


async def maybe_store_info(
    mm: MemoryManager | None,
    message: str,
    registry: "Registry | None" = None,  # unused — kept for backward compat
) -> None:
    """Extract facts from ``message`` via pattern matching and route to memory.

    Pure middleware — no LLM. Same routing as the old LLM-based
    implementation. ``registry`` is unused but preserved for
    backward compat with ``mem_inject.mem_turn``.
    """
    if mm is None or not message or not message.strip():
        return
    try:
        facts = _extract_kv(message) + _extract_named_entities(message)
        if not facts:
            return
        # Dedupe (key, value) pairs.
        deduped: list[ScoredFact] = []
        seen_pairs: set[tuple[str, str]] = set()
        for f in facts:
            pair = (f["key"].strip().lower(), f["value"].strip().lower())
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            deduped.append(f)
        explicit = [f for f in deduped if f.get("kind") == "explicit"]
        inferred = [f for f in deduped if f.get("kind") == "inferred"]
        explicit_whitelisted = [f for f in explicit if f["key"].strip().lower() in _ALLOWED_KEYS]
        explicit_other = [f for f in explicit if f["key"].strip().lower() not in _ALLOWED_KEYS]
        if explicit_whitelisted:
            current = await mm.get_block(GOAT_ROLE, _KEY) or ""
            valid = {
                f["key"]: f["value"] for f in explicit_whitelisted
                if _GUARD.validate(f["key"], f["value"], "explicit", current)["decision"] == "allowed"
            }
            if valid:
                await mm.set_block(GOAT_ROLE, _KEY, _merge(current, valid))
        if explicit_other:
            await _store_in_chroma(mm, explicit_other)
        if inferred:
            await _store_inferred(mm, inferred)
    except Exception:
        pass
