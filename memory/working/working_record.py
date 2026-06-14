from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final, NotRequired, TypedDict

from memory.shared.types import (
    AgentRole, EntryId, IsoTimestamp, MemoryEntry,
    MemoryEntryMetadata, MemoryKey, MemorySource,
)

log = logging.getLogger("goat2.memory.working.working_record")

_SOURCE: Final[MemorySource] = "working"


class RecordDict(TypedDict):
    """Wire format stored by every working-memory backend.

    The first eight fields are always present. The trailing timestamp/access
    fields are ``NotRequired`` so records written before this schema (and the
    many literal RecordDict construction sites across the codebase) remain
    valid — readers must use ``.get()`` with sensible defaults.
    """
    id:            str
    agent_role:    str
    key:           str
    content:       str
    metadata:      dict[str, str | int | float | bool | list[str]]
    created_at:    str
    created_at_ts: float
    expires_at:    float | None
    updated_at:     NotRequired[str]
    updated_at_ts:  NotRequired[float]
    accessed_at_ts: NotRequired[float]
    access_count:   NotRequired[int]


@dataclass(slots=True)
class _Record:
    """Typed in-memory snapshot of a RecordDict with NewType domain primitives."""
    id:            EntryId
    agent_role:    AgentRole
    key:           MemoryKey
    content:       str
    metadata:      MemoryEntryMetadata
    created_at:    IsoTimestamp
    created_at_ts: float
    expires_at:    float | None


def _record_to_entry(r: _Record) -> MemoryEntry:
    # Pure — PyO3 candidate: fn record_to_entry(r: &Record) -> MemoryEntry
    return MemoryEntry(
        id=r.id, agent_role=r.agent_role, key=r.key, content=r.content,
        metadata=r.metadata, created_at=r.created_at, source=_SOURCE,
    )


def _dict_to_record(d: RecordDict) -> _Record:
    # Pure — PyO3 candidate: fn dict_to_record(d: &RecordDict) -> Record
    raw_meta = d.get("metadata") or {}
    tags     = raw_meta.get("tags") or []
    if isinstance(tags, str):
        tags = [t for t in tags.split(",") if t]
    meta = MemoryEntryMetadata(
        tags=list(tags),
        **{k: v for k, v in raw_meta.items()  # type: ignore[misc]
           if k != "tags" and isinstance(v, (str, int, float, bool))},
    )
    return _Record(
        id=EntryId(d["id"]),
        agent_role=AgentRole(d["agent_role"]),
        key=MemoryKey(d["key"]),
        content=d["content"],
        metadata=meta,
        created_at=IsoTimestamp(d.get("created_at") or ""),
        created_at_ts=float(d.get("created_at_ts") or 0),
        expires_at=d.get("expires_at"),
    )


def _record_to_dict(r: _Record) -> RecordDict:
    # Pure — PyO3 candidate: fn record_to_dict(r: &Record) -> RecordDict
    raw_meta: dict[str, str | int | float | bool | list[str]] = {
        k: v for k, v in r.metadata.items()  # type: ignore[assignment]
        if isinstance(v, (str, int, float, bool, list))
    }
    return RecordDict(
        id=str(r.id), agent_role=str(r.agent_role), key=str(r.key),
        content=r.content, metadata=raw_meta, created_at=str(r.created_at),
        created_at_ts=r.created_at_ts, expires_at=r.expires_at,
    )


def stamp_on_write(record: RecordDict, now_ts: float, now_iso: str) -> RecordDict:
    """Add update/access timestamps to a record at store time (mutates and returns).

    A store is a fresh write, so ``access_count`` starts at 0 and
    ``accessed_at_ts`` equals the write time. ``updated_at`` mirrors the write.
    """
    record["updated_at"] = now_iso
    record["updated_at_ts"] = now_ts
    record["accessed_at_ts"] = now_ts
    record["access_count"] = 0
    log.debug("stamp_on_write: key=%s ts=%.3f", record.get("key"), now_ts)
    return record


def stamp_on_read(record: RecordDict, now_ts: float) -> RecordDict:
    """Bump ``accessed_at_ts`` and increment ``access_count`` on read.

    Tolerant of records that predate the access schema (missing fields default
    to 0). Mutates and returns the record.
    """
    record["accessed_at_ts"] = now_ts
    record["access_count"] = int(record.get("access_count", 0) or 0) + 1
    log.debug("stamp_on_read: key=%s access_count=%d", record.get("key"), record["access_count"])
    return record
