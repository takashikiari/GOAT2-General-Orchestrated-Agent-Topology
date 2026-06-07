from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Final

from config.limits import WORKING_MEMORY_TTL
from memory.types import (
    AgentRole, EntryId, IsoTimestamp, MemoryEntry, MemoryEntryMetadata, MemoryKey,
)
from memory.working_backend import StorageBackend
from memory.working_record import _Record, _dict_to_record, _record_to_dict, _record_to_entry

log = logging.getLogger("goat2.memory.working")
_SOURCE: Final[str] = "working"


class WorkingCrudMixin:
    """store / retrieve / delete / clear / health for WorkingMemoryLayer."""

    backend:     StorageBackend
    default_ttl: int

    async def store(  # type: ignore[override]
        self, agent_role: AgentRole, key: MemoryKey, content: str,
        *, metadata: MemoryEntryMetadata | None = None, ttl: int | None = None,
    ) -> MemoryEntry:
        """
        Upsert content under key. TTL resolves as: kwarg > metadata["ttl"] > default_ttl > 0 (no expiry).
        metadata["ttl"] is consumed here and never written to the stored record.
        """
        meta = dict(metadata) if metadata else {}
        if ttl is None:
            ttl = int(meta.pop("ttl", self.default_ttl or WORKING_MEMORY_TTL))
        else:
            meta.pop("ttl", None)
        now_ts   = time.time()
        now_iso  = IsoTimestamp(datetime.fromtimestamp(now_ts, tz=timezone.utc).isoformat())
        entry_id = EntryId(f"wm-{uuid.uuid4().hex[:12]}")
        expires  = (now_ts + ttl) if ttl > 0 else None
        typed_meta = MemoryEntryMetadata(
            tags=list(meta.pop("tags", []) or []),
            **{k: v for k, v in meta.items()  # type: ignore[misc]
               if isinstance(v, (str, int, float, bool))},
        )
        record = _record_to_dict(_Record(
            id=entry_id, agent_role=agent_role, key=key, content=content,
            metadata=typed_meta, created_at=now_iso,
            created_at_ts=now_ts, expires_at=expires,
        ))
        await self.backend.set(agent_role, key, record, expires_at=expires)
        log.debug("store(%s, %s) ttl=%s", agent_role, key, f"{ttl}s" if ttl else "none")
        return MemoryEntry(
            id=entry_id, agent_role=agent_role, key=key, content=content,
            metadata=typed_meta, created_at=now_iso, source=_SOURCE,
        )

    async def retrieve(
        self, agent_role: AgentRole, key: MemoryKey,
    ) -> MemoryEntry | None:
        """O(1) key lookup. Returns None if absent or expired."""
        record = await self.backend.get(agent_role, key)
        return None if record is None else _record_to_entry(_dict_to_record(record))

    async def delete(self, agent_role: AgentRole, key: MemoryKey) -> bool:
        return await self.backend.delete(agent_role, key)

    async def clear(self, agent_role: AgentRole) -> int:
        count = await self.backend.flush(agent_role)
        log.debug("clear(%s): removed %d entries", agent_role, count)
        return count

    async def health(self) -> bool:
        return await self.backend.ping()
