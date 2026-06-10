"""Core types for the GOAT 2.0 memory system.

All types are Rust-ready with explicit type hints, NewType wrappers for
semantic distinction, and Protocol interfaces for structural typing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, NewType, NotRequired, Required, TypedDict
from typing import Protocol, runtime_checkable

__all__ = [
    "AgentRole",
    "MemoryKey",
    "EntryId",
    "IsoTimestamp",
    "EpochSeconds",
    "LettaAgentId",
    "MemorySource",
    "MemoryEntryMetadata",
    "MemoryEntry",
    "MemoryLayer",
]

# Semantic string types — Rust equivalent: newtype wrappers
AgentRole = NewType("AgentRole", str)
MemoryKey = NewType("MemoryKey", str)
EntryId = NewType("EntryId", str)
IsoTimestamp = NewType("IsoTimestamp", str)
EpochSeconds = NewType("EpochSeconds", float)
LettaAgentId = NewType("LettaAgentId", str)

MemorySource = Literal["working", "chroma", "letta", "fallback"]


class MemoryEntryMetadata(TypedDict, total=False):
    """
    Metadata carried by every MemoryEntry.

    `tags` is always present; other fields are backend-specific.
    Rust equivalent: struct with optional fields.
    """

    tags: Required[list[str]]
    created_at_ts: NotRequired[float]
    session: NotRequired[str]
    score: NotRequired[float]  # cosine similarity [0, 1]; set by ChromaDB
    expires_at_ts: NotRequired[float]  # epoch-seconds; checked by callers


@dataclass(slots=True, frozen=True)
class MemoryEntry:
    """
    Universal value type returned by all three memory layers.

    Treat as immutable (frozen=True). Rust equivalent: struct with fields.
    """

    id: EntryId
    agent_role: AgentRole
    key: MemoryKey
    content: str
    metadata: MemoryEntryMetadata
    created_at: IsoTimestamp
    source: MemorySource

    @property
    def ok(self) -> bool:
        """True when content is non-empty."""
        return bool(self.content)


@runtime_checkable
class MemoryLayer(Protocol):
    """
    Structural interface shared by all three memory backends.

    Matches Rust trait semantics — conformance is structural, not nominal.
    All methods are async for consistency with I/O operations.
    """

    async def store(
        self,
        agent_role: AgentRole,
        key: MemoryKey,
        content: str,
        *,
        metadata: MemoryEntryMetadata | None = None,
        ttl: int | None = None,
    ) -> MemoryEntry:
        """Persist content; returns the created MemoryEntry."""
        ...

    async def retrieve(
        self, agent_role: AgentRole, key: MemoryKey
    ) -> MemoryEntry | None:
        """Retrieve by exact key; None if not found."""
        ...

    async def search(
        self,
        agent_role: AgentRole,
        query: str,
        *,
        limit: int = 5,
        tags: list[str] | None = None,
    ) -> list[MemoryEntry]:
        """Semantic/search query; returns ranked results."""
        ...

    async def delete(
        self, agent_role: AgentRole, key: MemoryKey
    ) -> bool:
        """Delete by key; True if existed."""
        ...

    async def list(
        self, agent_role: AgentRole, *, limit: int = 20
    ) -> list[MemoryEntry]:
        """List entries for agent_role; limited."""
        ...

    async def clear(self, agent_role: AgentRole) -> int:
        """Clear all entries for agent_role; returns count."""
        ...

    async def health(self) -> bool:
        """Health check; True if operational."""
        ...
