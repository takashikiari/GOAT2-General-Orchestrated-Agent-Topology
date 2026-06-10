"""Detect key-value facts in user messages; route explicit→Letta, inferred→ChromaDB.

Strict ALLOWED_KEYS whitelist prevents Letta human block from accumulating garbage.
Non-whitelisted explicit facts go to ChromaDB with 7-day TTL; inferred non-whitelisted
facts are discarded entirely.

REGISTRY INJECTION (PHASE 4):
=============================
maybe_store_info() now requires `registry` parameter.
Uses registry.settings.agents.get() for model access.
"""
from __future__ import annotations

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
from supervisor.llm_utils import _call_llm, _extract_json

__all__ = ["maybe_store_info"]

_KEY:  Final[str] = "human"
_GUARD = PollutionGuard()

# Strict whitelist for Letta core memory (human block). Only these keys are allowed.
# All other keys are either stored in ChromaDB (explicit) or discarded (inferred).
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
_SYSTEM: Final[str] = (
    "Extract factual key-value pairs about the user. For each pair set "
    "kind='explicit' (user stated it directly) or 'inferred' (deduced from context). "
    "Return JSON {\"facts\":[{\"key\":\"k\",\"value\":\"v\",\"kind\":\"explicit\"}]} or {\"facts\":[]}. "
    "Never extract agent_id, passage_id, search_key, limit, offset, score, memory_type, "
    "internal identifiers, or facts not grounded in the message."
)


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
    """Persist non-whitelisted explicit facts to ChromaDB with 7-day TTL.

    Inferred facts that are not whitelisted are discarded entirely.
    """
    exp = time.time() + INFERRED_TTL
    for f in facts:
        nk = f["key"].strip().lower()
        # Skip if not in whitelist (inferred non-whitelisted → discard)
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
    registry: "Registry",
) -> None:
    """
    Route extracted facts: explicit→ALLOWED_KEYS→Letta or ChromaDB; inferred→ChromaDB or discard.

    Facts are routed as follows:
    - explicit + whitelisted → PollutionGuard → Letta human block
    - explicit + non-whitelisted → ChromaDB episodic with 7-day TTL
    - inferred + whitelisted → ChromaDB episodic with 7-day TTL
    - inferred + non-whitelisted → discarded entirely

    REGISTRY INJECTION (PHASE 4):
    =============================
    Requires registry parameter. Uses registry.settings.agents.get() for model access.
    """
    if mm is None:
        return
    try:
        raw = await _call_llm(
            registry.settings.agents.get("memory"),
            [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": message}],
            temperature=0.0, json_mode=True,
        )
        data  = _extract_json(raw)
        facts: list[ScoredFact] = data.get("facts", []) if isinstance(data, dict) else []
        if not facts:
            return
        explicit = [f for f in facts if f.get("kind") == "explicit"]
        inferred = [f for f in facts if f.get("kind") == "inferred"]
        
        # Separate whitelisted vs non-whitelisted explicit facts
        explicit_whitelisted = [f for f in explicit if f["key"].strip().lower() in _ALLOWED_KEYS]
        explicit_other = [f for f in explicit if f["key"].strip().lower() not in _ALLOWED_KEYS]
        
        if explicit_whitelisted:
            current = await mm.get_block(GOAT_ROLE, _KEY) or ""
            valid = {f["key"]: f["value"] for f in explicit_whitelisted
                     if _GUARD.validate(f["key"], f["value"], "explicit", current)["decision"] == "allowed"}
            if valid:
                await mm.set_block(GOAT_ROLE, _KEY, _merge(current, valid))
        
        # Non-whitelisted explicit facts → ChromaDB with 7-day TTL
        if explicit_other:
            await _store_in_chroma(mm, explicit_other)
        
        # Inferred facts → ChromaDB with 7-day TTL (non-whitelisted inferred are discarded in _store_inferred)
        if inferred:
            await _store_inferred(mm, inferred)
    except Exception:
        pass
