from __future__ import annotations

import logging

from memory.long_term.letta_helpers import _now_iso
from memory.shared.types import (
    AgentRole, EntryId, IsoTimestamp, MemoryEntry,
    MemoryEntryMetadata, MemoryKey,
)

log = logging.getLogger("goat2.memory.letta")


class _InContextFallback:
    """Pure in-memory store used when Letta is unreachable. Data is ephemeral."""

    __slots__ = ("_store", "_ctr")

    def __init__(self) -> None:
        self._store: dict[AgentRole, list[MemoryEntry]] = {}
        self._ctr: int = 0

    def _next_id(self) -> EntryId:
        # Pure given counter state — PyO3 candidate: fn next_id(ctr: &mut u64) -> EntryId
        self._ctr += 1
        return EntryId(f"fallback-{self._ctr:06d}")

    def _bucket(self, role: AgentRole) -> list[MemoryEntry]:
        if role not in self._store:
            self._store[role] = []
        return self._store[role]

    def store(
        self, agent_role: AgentRole, key: MemoryKey,
        content: str, metadata: MemoryEntryMetadata | None,
    ) -> MemoryEntry:
        bucket = self._bucket(agent_role)
        self._store[agent_role] = [e for e in bucket if e.key != key]
        entry = MemoryEntry(
            id=self._next_id(), agent_role=agent_role, key=key, content=content,
            metadata=metadata or MemoryEntryMetadata(tags=[]),
            created_at=IsoTimestamp(_now_iso()), source="fallback",
        )
        self._store[agent_role].append(entry)
        log.debug("fallback.store: role=%s key=%s", agent_role, key)
        return entry

    def retrieve(self, agent_role: AgentRole, key: MemoryKey) -> MemoryEntry | None:
        for entry in reversed(self._bucket(agent_role)):
            if entry.key == key:
                return entry
        return None

    def search(
        self, agent_role: AgentRole, query: str,
        limit: int, tags: list[str] | None,
    ) -> list[MemoryEntry]:
        needle  = query.lower()
        tag_set = set(tags) if tags else set()
        results: list[MemoryEntry] = []
        for entry in reversed(self._bucket(agent_role)):
            if needle not in entry.content.lower() and needle not in entry.key.lower():
                continue
            stored = set(entry.metadata.get("tags") or [])
            if tag_set and not tag_set.issubset(stored):
                continue
            results.append(entry)
            if len(results) >= limit:
                break
        return results

    def delete(self, agent_role: AgentRole, key: MemoryKey) -> bool:
        before = len(self._bucket(agent_role))
        self._store[agent_role] = [e for e in self._bucket(agent_role) if e.key != key]
        return len(self._store[agent_role]) < before

    def list(self, agent_role: AgentRole, limit: int) -> list[MemoryEntry]:
        return list(reversed(self._bucket(agent_role)))[:limit]

    def clear(self, agent_role: AgentRole) -> int:
        count = len(self._bucket(agent_role))
        self._store[agent_role] = []
        return count
