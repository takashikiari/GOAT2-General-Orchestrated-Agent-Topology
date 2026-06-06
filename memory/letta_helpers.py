from __future__ import annotations

from datetime import datetime, timezone
from typing import Final, TypedDict

from memory.types import (
    AgentRole, EntryId, IsoTimestamp, MemoryEntry, MemoryEntryMetadata, MemoryKey,
)

__all__ = [
    "_LettaPassage", "_LettaAgentInfo", "_LettaBlockValue",
    "_HEALTH_CHECK_INTERVAL", "_HTTP_TIMEOUT", "_AGENT_NAME_PREFIX", "_GLOBAL_TAG",
    "_role_tag", "_key_tag", "_passage_text", "_parse_passage_text",
    "_now_iso", "_extract_passages", "_passage_to_entry",
]

class _LettaPassage(TypedDict, total=False):
    id:         str
    text:       str
    content:    str
    tags:       list[str]
    created_at: str
    timestamp:  str


class _LettaAgentInfo(TypedDict):
    id:   str
    name: str


class _LettaBlockValue(TypedDict, total=False):
    value: str
    label: str


_HEALTH_CHECK_INTERVAL: Final[float] = 30.0
_HTTP_TIMEOUT:          Final[float] = 12.0
_AGENT_NAME_PREFIX:     Final[str]   = "goat2-"
_GLOBAL_TAG:            Final[str]   = "goat2"


def _role_tag(role: AgentRole) -> str:
    # Pure — PyO3 candidate: fn role_tag(role: &str) -> String { format!("goat2:{role}") }
    return f"goat2:{role}"


def _key_tag(key: MemoryKey) -> str:
    # Pure — PyO3 candidate: fn key_tag(key: &str) -> String { format!("key:{key}") }
    return f"key:{key}"


def _passage_text(key: MemoryKey, content: str) -> str:
    # Pure — PyO3 candidate: fn passage_text(key: &str, content: &str) -> String
    return f"[KEY:{key}]\n{content}"


def _parse_passage_text(text: str) -> tuple[MemoryKey, str]:
    # Pure — PyO3 candidate: fn parse_passage_text(text: &str) -> (String, String)
    if text.startswith("[KEY:") and "]\n" in text:
        end     = text.index("]\n")
        key     = MemoryKey(text[5:end])
        content = text[end + 2:]
        return key, content
    return MemoryKey(""), text


def _now_iso() -> IsoTimestamp:
    # Pure given fixed instant — PyO3 candidate: fn now_iso() -> String { Utc::now().to_rfc3339() }
    return IsoTimestamp(datetime.now(timezone.utc).isoformat())


def _extract_passages(data: object) -> list[_LettaPassage]:
    # Not a PyO3 candidate — depends on Python dict dispatch semantics.
    if isinstance(data, list):
        return data  # type: ignore[return-value]
    if isinstance(data, dict):
        candidates = data.get("results") or data.get("passages") or []
        if isinstance(candidates, list):
            return candidates  # type: ignore[return-value]
    return []

def _passage_to_entry(p: dict, role: AgentRole, fallback: str = "") -> MemoryEntry:
    k, body = _parse_passage_text(p.get("text") or p.get("content") or "")
    return MemoryEntry(
        id=EntryId(p.get("id") or ""), agent_role=role,
        key=k or MemoryKey(fallback), content=body,
        metadata=MemoryEntryMetadata(tags=p.get("tags") or []),
        created_at=IsoTimestamp(p.get("timestamp") or p.get("created_at") or _now_iso()),
        source="letta",
    )
