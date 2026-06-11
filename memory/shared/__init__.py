"""Shared types and utilities for memory system.

Provides core types, enums, and utilities used across all memory layers.

EXPORTS:
- MemoryEntry: Universal value type for all memory tiers
- MemoryLayer: Protocol interface for memory backends
- MemoryType, MemoryTierLiteral, LayerStatus: Enums
- MemoryManager: Single entry-point for all memory operations
- auto_save_memory: Hook for automatic memory persistence
- PollutionGuard: Content quality validation
"""
from __future__ import annotations

import logging

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
from memory.shared.memory_enums import MemoryType, MemoryTierLiteral, LayerStatus
from memory.shared.memory_manager import MemoryManager
from memory.shared.hooks import auto_save_memory
from memory.shared.pollution_guard import PollutionGuard

log = logging.getLogger("goat2.memory.shared")

__all__ = [
    # Types
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
    # Enums
    "MemoryType",
    "MemoryTierLiteral",
    "LayerStatus",
    # Manager
    "MemoryManager",
    # Hooks & validation
    "auto_save_memory",
    "PollutionGuard",
]