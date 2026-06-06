from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, NewType, NotRequired, Required, TypedDict
from typing import Protocol, runtime_checkable

__all__ = [
    "AgentRole", "MemoryKey", "EntryId", "IsoTimestamp",
    "EpochSeconds", "LettaAgentId", "MemorySource",
    "MemoryEntryMetadata", "MemoryEntry", "MemoryLayer",
]

AgentRole    = NewType("AgentRole",    str)
MemoryKey    = NewType("MemoryKey",    str)
EntryId      = NewType("EntryId",      str)
IsoTimestamp = NewType("IsoTimestamp", str)
EpochSeconds = NewType("EpochSeconds", float)
LettaAgentId = NewType("LettaAgentId", str)

MemorySource = Literal["working", "chroma", "letta", "fallback"]


class MemoryEntryMetadata(TypedDict, total=False):
    """Metadata carried by every MemoryEntry. `tags` is always present; other fields are backend-specific."""
    tags:          Required[list[str]]
    created_at_ts: NotRequired[float]
    session:       NotRequired[str]
    score:         NotRequired[float]   # cosine similarity [0, 1]; set by ChromaDB search
    expires_at_ts: NotRequired[float]   # epoch-seconds expiry; checked by callers, not enforced by backend


@dataclass(slots=True)
class MemoryEntry:
    """Universal value type returned by all three memory layers. Treat as immutable."""
    id:         EntryId
    agent_role: AgentRole
    key:        MemoryKey
    content:    str
    metadata:   MemoryEntryMetadata
    created_at: IsoTimestamp
    source:     MemorySource

    @property
    def ok(self) -> bool:
        return bool(self.content)


@runtime_checkable
class MemoryLayer(Protocol):
    """
    Structural interface (Protocol) shared by all three memory backends.
    Matches Rust trait semantics — conformance is structural, not nominal.
    """

    async def store(
        self, agent_role: AgentRole, key: MemoryKey, content: str,
        *, metadata: MemoryEntryMetadata | None = None, ttl: int | None = None,
    ) -> MemoryEntry: ...

    async def retrieve(
        self, agent_role: AgentRole, key: MemoryKey,
    ) -> MemoryEntry | None: ...

    async def search(
        self, agent_role: AgentRole, query: str,
        *, limit: int = 5, tags: list[str] | None = None,
    ) -> list[MemoryEntry]: ...

    async def delete(self, agent_role: AgentRole, key: MemoryKey) -> bool: ...

    async def list(
        self, agent_role: AgentRole, *, limit: int = 20,
    ) -> list[MemoryEntry]: ...

    async def clear(self, agent_role: AgentRole) -> int: ...

    async def health(self) -> bool: ...
