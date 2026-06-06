"""Letta API helper functions for passage parsing and entry conversion.

Pure functions where possible — PyO3 candidates. Handles Letta-specific
data formats and converts to universal MemoryEntry type.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Final, TypedDict

from memory.types import (
    AgentRole,
    EntryId,
    IsoTimestamp,
    MemoryEntry,
    MemoryEntryMetadata,
    MemoryKey,
)

__all__ = [
    "_LettaPassage",
    "_LettaAgentInfo",
    "_LettaBlockValue",
    "_HEALTH_CHECK_INTERVAL",
    "_HTTP_TIMEOUT",
    "_AGENT_NAME_PREFIX",
    "_GLOBAL_TAG",
    "_role_tag",
    "_key_tag",
    "_passage_text",
    "_parse_passage_text",
    "_now_iso",
    "_extract_passages",
    "_passage_to_entry",
]


class _LettaPassage(TypedDict, total=False):
    """Letta passage response structure."""

    id: str
    text: str
    content: str
    tags: list[str]
    created_at: str
    timestamp: str


class _LettaAgentInfo(TypedDict):
    """Letta agent info response structure."""

    id: str
    name: str


class _LettaBlockValue(TypedDict, total=False):
    """Letta block value response structure."""

    value: str
    label: str


_HEALTH_CHECK_INTERVAL: Final[float] = 30.0
_HTTP_TIMEOUT: Final[float] = 12.0
_AGENT_NAME_PREFIX: Final[str] = "goat2-"
_GLOBAL_TAG: Final[str] = "goat2"


def _role_tag(role: AgentRole) -> str:
    """
    Generate role tag for Letta passage metadata.

    Pure — PyO3 candidate.
    """
    return f"goat2:{role}"


def _key_tag(key: MemoryKey) -> str:
    """
    Generate key tag for Letta passage metadata.

    Pure — PyO3 candidate.
    """
    return f"key:{key}"


def _passage_text(key: MemoryKey, content: str) -> str:
    """
    Format passage text with key prefix for parsing.

    Pure — PyO3 candidate.
    """
    return f"[KEY:{key}]\n{content}"


def _parse_passage_text(text: str) -> tuple[MemoryKey, str]:
    """
    Parse passage text to extract key and content.

    Pure — PyO3 candidate.
    """
    if text.startswith("[KEY:") and "]\n" in text:
        end = text.index("]\n")
        key = MemoryKey(text[5:end])
        content = text[end + 2:]
        return key, content
    return MemoryKey(""), text


def _now_iso() -> IsoTimestamp:
    """
    Current UTC timestamp in ISO 8601 format.

    Pure given fixed instant — PyO3 candidate.
    """
    return IsoTimestamp(datetime.now(timezone.utc).isoformat())


def _extract_passages(data: object) -> list[_LettaPassage]:
    """
    Extract passage list from Letta API response.

    Not a PyO3 candidate — depends on Python dict dispatch semantics.
    """
    if isinstance(data, list):
        return data  # type: ignore[return-value]
    if isinstance(data, dict):
        candidates = data.get("results") or data.get("passages") or []
        if isinstance(candidates, list):
            return candidates  # type: ignore[return-value]
    return []


def _passage_to_entry(
    p: dict, role: AgentRole, fallback: str = ""
) -> MemoryEntry:
    """
    Convert Letta passage dict to MemoryEntry.

    Handles missing fields with sensible defaults.
    """
    k, body = _parse_passage_text(p.get("text") or p.get("content") or "")
    return MemoryEntry(
        id=EntryId(p.get("id") or ""),
        agent_role=role,
        key=k or MemoryKey(fallback),
        content=body,
        metadata=MemoryEntryMetadata(tags=p.get("tags") or []),
        created_at=IsoTimestamp(
            p.get("timestamp") or p.get("created_at") or _now_iso()
        ),
        source="letta",
    )
