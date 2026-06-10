from __future__ import annotations

import logging
import time

from memory.shared.types import AgentRole, MemoryEntry, MemoryKey
from memory.working.working_backend import StorageBackend
from memory.working.working_record import _dict_to_record, _record_to_entry
from memory.working.working_search import _entry_has_all_tags, _score, _tokenize

log = logging.getLogger("goat2.memory.working")


class WorkingQueryMixin:
    """search / list / ttl_of / count for WorkingMemoryLayer. All reads only."""

    backend: StorageBackend

    async def search(
        self, agent_role: AgentRole, query: str,
        *, limit: int = 5, tags: list[str] | None = None,
    ) -> list[MemoryEntry]:
        """
        Token-overlap keyword search across all live entries for agent_role.
        Scored by content hit-rate + key-match bonus; ties broken by recency.
        """
        all_keys = await self.backend.keys(agent_role)
        if not all_keys:
            return []
        query_terms                            = _tokenize(query)
        scored: list[tuple[float, MemoryEntry]] = []
        for k in all_keys:
            record = await self.backend.get(agent_role, k)
            if record is None:
                continue
            entry = _record_to_entry(_dict_to_record(record))
            if tags and not _entry_has_all_tags(entry, tags):
                continue
            sc = _score(query_terms, entry.content, entry.key)
            if sc > 0:
                scored.append((sc, entry))
        scored.sort(
            key=lambda x: (x[0], float(x[1].metadata.get("created_at_ts") or 0)),
            reverse=True,
        )
        return [e for _, e in scored[:limit]]

    async def list(
        self, agent_role: AgentRole, *, limit: int = 20,
    ) -> list[MemoryEntry]:
        """Return up to `limit` entries for agent_role sorted newest-first."""
        all_keys = await self.backend.keys(agent_role)
        if not all_keys:
            return []
        entries: list[MemoryEntry] = []
        for k in all_keys:
            record = await self.backend.get(agent_role, k)
            if record is None:
                continue
            entries.append(_record_to_entry(_dict_to_record(record)))
        entries.sort(
            key=lambda e: float(e.metadata.get("created_at_ts") or 0.0), reverse=True
        )
        return entries[:limit]

    async def ttl_of(self, agent_role: AgentRole, key: MemoryKey) -> float | None:
        """Remaining TTL in seconds; None = never expires; -1.0 = absent or already expired."""
        record = await self.backend.get(agent_role, key)
        if record is None:
            return -1.0
        expires_at = record.get("expires_at")
        if expires_at is None:
            return None
        return max(0.0, expires_at - time.time())

    async def count(self, agent_role: AgentRole) -> int:
        return len(await self.backend.keys(agent_role))
