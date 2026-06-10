"""Backward compatibility shim for memory.types.

This module has been moved to memory.shared.types.
"""
from memory.shared.types import (
    AgentRole,
    MemoryKey,
    EntryId,
    IsoTimestamp,
    EpochSeconds,
    LettaAgentId,
    MemorySource,
    MemoryEntryMetadata,
    MemoryEntry,
    MemoryLayer,
)

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