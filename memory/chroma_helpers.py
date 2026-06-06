from __future__ import annotations

import re
import time
from datetime import datetime, timezone

from memory.chroma_types import ChromaStoredMetadata, _COLLECTION_PREFIX
from memory.types import AgentRole, EntryId, IsoTimestamp, MemoryEntry, MemoryEntryMetadata, MemoryKey

__all__ = [
    "_sanitize_key", "_doc_id", "_collection_name",
    "_tags_to_str", "_str_to_tags", "_now_iso", "_now_ts",
    "_has_all_tags", "_build_chroma_metadata",
]


def _sanitize_key(key: MemoryKey) -> str:
    # Pure — PyO3 candidate: fn sanitize_key(key: &str) -> String
    return re.sub(r"[^a-zA-Z0-9_-]", "_", key)[:100]


def _doc_id(role: AgentRole, key: MemoryKey) -> EntryId:
    # Pure — PyO3 candidate: fn doc_id(role: &str, key: &str) -> String
    return EntryId(f"{_COLLECTION_PREFIX}{role}_{_sanitize_key(key)}")


def _collection_name(role: AgentRole) -> str:
    # Pure — PyO3 candidate: fn collection_name(role: &str) -> String
    return f"{_COLLECTION_PREFIX}{role}"


def _tags_to_str(tags: list[str] | None) -> str:
    # Pure — PyO3 candidate: fn tags_to_str(tags: &[&str]) -> String
    if not tags:
        return ""
    return ",".join(t.strip() for t in tags if t.strip())


def _str_to_tags(tags_str: str) -> list[str]:
    # Pure — PyO3 candidate: fn str_to_tags(s: &str) -> Vec<String>
    if not tags_str:
        return []
    return [t for t in tags_str.split(",") if t]


def _now_iso() -> IsoTimestamp:
    # Pure given fixed instant — PyO3 candidate: fn now_iso() -> String
    return IsoTimestamp(datetime.now(timezone.utc).isoformat())


def _now_ts() -> int:
    # Pure given fixed instant — PyO3 candidate: fn now_ts() -> i64
    return int(time.time())


def _has_all_tags(entry: MemoryEntry, required: list[str]) -> bool:
    # Pure — PyO3 candidate: fn has_all_tags(stored: &[&str], required: &[&str]) -> bool
    raw = entry.metadata.get("tags") or []
    stored: set[str] = set(raw if isinstance(raw, list) else _str_to_tags(raw))
    return all(t in stored for t in required)


def _build_chroma_metadata(
    agent_role: AgentRole, key: MemoryKey,
    user_meta: MemoryEntryMetadata | None, ts: int, iso: IsoTimestamp,
) -> ChromaStoredMetadata:
    # Pure — PyO3 candidate: fn build_chroma_metadata(...) -> ChromaStoredMetadata
    tags_str = _tags_to_str(list(user_meta.get("tags") or []) if user_meta else [])
    return ChromaStoredMetadata(
        agent_role=str(agent_role), key=str(key),
        created_at=str(iso), created_at_ts=ts, tags=tags_str,
    )
