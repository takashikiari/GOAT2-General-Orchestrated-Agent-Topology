from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Final, TYPE_CHECKING

from config.limits import WORKING_MEMORY_TTL
from memory.shared.types import (
    AgentRole, EntryId, IsoTimestamp, MemoryEntry, MemoryEntryMetadata, MemoryKey,
)
from memory.working.working_record import (
    _Record, _dict_to_record, _record_to_dict, _record_to_entry,
    stamp_on_read, stamp_on_write,
)

if TYPE_CHECKING:
    from memory.working.backend_protocol import WorkingMemoryBackend

log = logging.getLogger("goat2.memory.working.working_crud")
_SOURCE: Final[str] = "working"

__all__ = ["WorkingCrudMixin"]


class WorkingCrudMixin:
    """store / retrieve / delete / clear / health for WorkingMemoryLayer."""

    backend:     "WorkingMemoryBackend"
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
        stamp_on_write(record, now_ts, now_iso)
        await self.backend.set(str(agent_role), str(key), record, expires)
        await self._sync_last_write_to_redis()
        log.debug("store(%s, %s) ttl=%s", agent_role, key, f"{ttl}s" if ttl else "none")
        return MemoryEntry(
            id=entry_id, agent_role=agent_role, key=key, content=content,
            metadata=typed_meta, created_at=now_iso, source=_SOURCE,
        )

    async def _sync_last_write_to_redis(self) -> None:
        """Update Redis last-write timestamp for working tier (fail-silent)."""
        try:
            r = await self.backend._get_redis()  # type: ignore[attr-defined]
            iso_now = IsoTimestamp(datetime.now(timezone.utc).isoformat())
            await r.set("goat2:working:last_write:working", iso_now)  # type: ignore[union-attr]
        except Exception as exc:
            log.debug("Working last-write sync failed (non-blocking): %s", exc)

    async def retrieve(
        self, agent_role: AgentRole, key: MemoryKey,
    ) -> MemoryEntry | None:
        """O(1) key lookup. Returns None if absent or expired.

        On a hit, bumps ``accessed_at_ts`` and ``access_count`` and writes the
        record back, preserving its original ``expires_at`` so the access update
        never extends or shortens the TTL. Write-back failures are non-fatal.
        """
        record = await self.backend.get(str(agent_role), str(key))
        if record is None:
            return None
        stamp_on_read(record, time.time())
        try:
            await self.backend.set(str(agent_role), str(key), record, record.get("expires_at"))
        except Exception as exc:
            log.debug("retrieve(%s, %s): access write-back skipped: %s", agent_role, key, exc)
        log.debug("retrieve(%s, %s) access_count=%s", agent_role, key, record.get("access_count"))
        return _record_to_entry(_dict_to_record(record))

    async def delete(self, agent_role: AgentRole, key: MemoryKey) -> bool:
        return await self.backend.delete(str(agent_role), str(key))

    async def clear(self, agent_role: AgentRole) -> int:
        count = await self.backend.flush(str(agent_role))
        log.debug("clear(%s): removed %d entries", agent_role, count)
        return count

    async def health(self) -> bool:
        return await self.backend.ping()

    async def list(self, agent_role: AgentRole, limit: int = 50) -> list:
        """Return up to `limit` most recent entries for agent_role."""
        from memory.shared.types import MemoryEntry
        try:
            keys = await self.backend.keys(str(agent_role))
        except AttributeError:
            return []
        entries = []
        for key in keys[-limit:]:
            record = await self.backend.get(str(agent_role), str(key))
            if record:
                meta = dict(record.get("metadata") or {})
                # created_at_ts lives at the top level of RecordDict, not inside
                # metadata. Mirror it into metadata so filter_by_time can use it.
                meta.setdefault("created_at_ts", float(record.get("created_at_ts") or 0))
                entries.append(MemoryEntry(
                    id=str(record.get("id") or uuid.uuid4()),
                    agent_role=agent_role,
                    key=key,
                    content=record.get("content", ""),
                    metadata=meta,  # type: ignore[arg-type]
                    created_at=record.get("created_at", ""),
                    source=_SOURCE,
                ))
        return entries
