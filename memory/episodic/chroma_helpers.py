"""ChromaDB helper functions for ID generation, metadata, and tags.

All functions are pure — PyO3 candidates. No side effects, deterministic
output for given inputs.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone

from memory.episodic.chroma_types import ChromaStoredMetadata, _COLLECTION_PREFIX
from memory.shared.types import (
    AgentRole,
    EntryId,
    IsoTimestamp,
    MemoryEntry,
    MemoryEntryMetadata,
    MemoryKey,
)

log = logging.getLogger("goat2.memory.chroma")

__all__ = [
    "_sanitize_key",
    "_doc_id",
    "_collection_name",
    "_tags_to_str",
    "_str_to_tags",
    "_now_iso",
    "_now_ts",
    "_has_all_tags",
    "_build_chroma_metadata",
]


def _sanitize_key(key: MemoryKey) -> str:
    """
    Sanitize key for ChromaDB document ID.

    Replaces non-alphanumeric characters with underscores, truncates to 100.
    Pure — PyO3 candidate.
    """
    return re.sub(r"[^a-zA-Z0-9_-]", "_", key)[:100]


def _doc_id(role: AgentRole, key: MemoryKey) -> EntryId:
    """
    Generate deterministic document ID from role and key.

    Pure — PyO3 candidate.
    """
    return EntryId(f"{_COLLECTION_PREFIX}{role}_{_sanitize_key(key)}")


def _collection_name(role: AgentRole) -> str:
    """
    Generate collection name from agent role.

    Pure — PyO3 candidate.
    """
    return f"{_COLLECTION_PREFIX}{role}"


def _tags_to_str(tags: list[str] | None) -> str:
    """
    Convert tag list to comma-separated string.

    Pure — PyO3 candidate.
    """
    if not tags:
        return ""
    return ",".join(t.strip() for t in tags if t.strip())


def _str_to_tags(tags_str: str) -> list[str]:
    """
    Convert comma-separated string to tag list.

    Pure — PyO3 candidate.
    """
    if not tags_str:
        return []
    return [t for t in tags_str.split(",") if t]


def _now_iso() -> IsoTimestamp:
    """
    Current UTC timestamp in ISO 8601 format.

    Pure given fixed instant — PyO3 candidate.
    """
    return IsoTimestamp(datetime.now(timezone.utc).isoformat())


def _now_ts() -> int:
    """
    Current Unix timestamp as integer.

    Pure given fixed instant — PyO3 candidate.
    """
    return int(time.time())


def _has_all_tags(entry: MemoryEntry, required: list[str]) -> bool:
    """
    Check if entry has all required tags.

    Pure — PyO3 candidate.
    """
    raw = entry.metadata.get("tags") or []
    stored: set[str] = set(
        raw if isinstance(raw, list) else _str_to_tags(raw)
    )
    return all(t in stored for t in required)


def _build_chroma_metadata(
    agent_role: AgentRole,
    key: MemoryKey,
    user_meta: MemoryEntryMetadata | None,
    ts: int,
    iso: IsoTimestamp,
) -> ChromaStoredMetadata:
    """
    Build episodic metadata dict from components, including the full timestamp
    schema (created/updated/accessed + access_count) and the compartment.

    ``compartment`` and ``permanent`` are carried from ``user_meta`` when present.
    A fresh write starts at ``access_count=0`` with ``accessed_at_ts == created``.
    Pure — PyO3 candidate.
    """
    tags_str = _tags_to_str(
        list(user_meta.get("tags") or []) if user_meta else []
    )
    compartment = str(user_meta.get("compartment", "")) if user_meta else ""
    permanent = bool(user_meta.get("permanent", False)) if user_meta else False
    return ChromaStoredMetadata(
        agent_role=str(agent_role),
        key=str(key),
        created_at=str(iso),
        created_at_ts=ts,
        tags=tags_str,
        updated_at=str(iso),
        updated_at_ts=float(ts),
        accessed_at_ts=float(ts),
        access_count=0,
        compartment=compartment,
        permanent=permanent,
    )
