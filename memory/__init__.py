"""Memory system — Three-tier architecture (working, episodic, long-term).

This module re-exports all public classes from subdirectories for backward
compatibility. New code should import directly from subdirectories:

    from memory.working import WorkingMemoryLayer
    from memory.episodic import ChromaMemoryClient
    from memory.long_term import LettaClient
    from memory.shared import MemoryManager, MemoryEntry

SUBDIRECTORIES:
- working/: Redis-backed session-scoped storage
- episodic/: ChromaDB semantic storage
- long_term/: Letta API integration
- temporal/: Time-based search and filtering
- shared/: Types, enums, and utilities
- router/: Memory routing and classification
"""
# Re-export from subdirectories for backward compatibility
from memory.shared.types import (
    MemoryEntry,
    MemoryLayer,
    AgentRole,
    MemoryKey,
    EntryId,
    IsoTimestamp,
    MemoryEntryMetadata,
)
from memory.shared.memory_enums import MemoryType, MemoryTierLiteral, LayerStatus
from memory.long_term.letta_client import LettaClient
from memory.episodic.chromadb_client import ChromaMemoryClient
from memory.working.working_memory import WorkingMemoryLayer
from memory.working.working_backend import StorageBackend
from memory.working.dict_backend import DictBackend
from memory.working.redis_backend import RedisBackend
from memory.working.working_record import RecordDict
from memory.shared.memory_manager import MemoryManager
from memory.shared.hooks import auto_save_memory

__all__ = [
    # Shared types
    "MemoryEntry",
    "MemoryLayer",
    "AgentRole",
    "MemoryKey",
    "EntryId",
    "IsoTimestamp",
    "MemoryEntryMetadata",
    # Enums
    "MemoryType",
    "MemoryTierLiteral",
    "LayerStatus",
    # Long-term
    "LettaClient",
    # Episodic
    "ChromaMemoryClient",
    # Working
    "WorkingMemoryLayer",
    "StorageBackend",
    "DictBackend",
    "RedisBackend",
    "RecordDict",
    # Manager
    "MemoryManager",
    # Hooks
    "auto_save_memory",
]