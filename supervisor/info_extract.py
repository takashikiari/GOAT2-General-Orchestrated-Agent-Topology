"""Detect key-value facts in user messages; route explicit→Letta, inferred→ChromaDB."""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from memory.memory_manager import MemoryManager

from config.settings import settings
from memory.memory_enums import MemoryType
from memory.pollution_guard import PollutionGuard
from memory.types import MemoryEntryMetadata
from supervisor.info_types import INFERRED_TTL, ScoredFact
from supervisor.llm_utils import _call_llm, _extract_json

__all__ = ["maybe_store_info"]

_ROLE: Final[str] = "goat"
_KEY:  Final[str] = "human"
_GUARD = PollutionGuard()
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
            if k not in _BLOCKED and not k.endswith("_id"):
                index[k] = line
    for k, v in new_pairs.items():
        nk = k.strip().lower()
        if nk not in _BLOCKED and not nk.endswith("_id"):
            index[nk] = f"{k.strip()}: {v.strip()}"
    return "\n".join(index.values())


async def _store_inferred(mm: MemoryManager, facts: list[ScoredFact]) -> None:
    """Persist inferred facts to ChromaDB tagged 'inferred' with expires_at_ts = now + 7 days."""
    exp = time.time() + INFERRED_TTL
    for f in facts:
        nk = f["key"].strip().lower()
        if nk in _BLOCKED or nk.endswith("_id"):
            continue
        meta = MemoryEntryMetadata(tags=["inferred"], expires_at_ts=exp)
        await mm.store(
            _ROLE, f"inferred:{nk}", f"{nk}: {f['value'].strip()}",
            memory_type=MemoryType.EPISODIC, metadata=meta,
        )


async def maybe_store_info(mm: MemoryManager | None, message: str) -> None:
    """Route extracted facts: explicit→PollutionGuard→Letta; inferred→ChromaDB 7-day TTL."""
    if mm is None:
        return
    try:
        raw = await _call_llm(
            settings.agents.get("memory"),
            [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": message}],
            temperature=0.0, json_mode=True,
        )
        data  = _extract_json(raw)
        facts: list[ScoredFact] = data.get("facts", []) if isinstance(data, dict) else []
        if not facts:
            return
        explicit = [f for f in facts if f.get("kind") == "explicit"]
        inferred = [f for f in facts if f.get("kind") == "inferred"]
        if explicit:
            current = await mm.get_block(_ROLE, _KEY) or ""
            valid = {f["key"]: f["value"] for f in explicit
                     if _GUARD.validate(f["key"], f["value"], "explicit", current)["decision"] == "allowed"}
            if valid:
                await mm.set_block(_ROLE, _KEY, _merge(current, valid))
        if inferred:
            await _store_inferred(mm, inferred)
    except Exception:
        pass
