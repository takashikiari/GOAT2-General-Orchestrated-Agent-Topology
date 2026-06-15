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
- memory_tools/: Tool definitions
- memory_metrics/: Health metrics and monitoring
- memory_promoter.py: Automatic tier promotion
- config.py: Memory-specific constants

DEBUG LOGGER NAMESPACES:
=======================
- goat2.memory.shared      — types, enums, manager, hooks, validation
- goat2.memory.working     — Redis/Dict backends, sweep, query, crud
- goat2.memory.chroma      — ChromaDB client, CRUD, query, parsers
- goat2.memory.letta       — Letta client, ops, registry, fallback
- goat2.memory.temporal    — time parser, filter, list, search
- goat2.memory.router      — classifier, cache, decision, executor
- goat2.memory.tools       — all 16 ToolDefinitions
- goat2.memory.metrics     — health counts
- goat2.memory.promoter    — turn-based promotion
"""
from __future__ import annotations

import logging

log = logging.getLogger("goat2.memory")

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
from memory.working.backend_protocol import WorkingMemoryBackend
from memory.working.dict_backend import DictBackend
from memory.working.redis_backend import RedisBackend
from memory.working.working_record import RecordDict
from memory.shared.memory_manager import MemoryManager
from memory.shared.hooks import auto_save_memory
from memory.router.router import MemoryRouter


# Re-export from memory_metrics
from memory.memory_metrics import (
    count_working_entries,
    count_episodic_entries,
    count_long_term_entries,
    memory_health_report,
)

# Re-export from config
from memory.config import (
    WORKING_BACKEND,
    EPISODIC_BACKEND,
    LONG_TERM_BACKEND,
    PROMOTION_TURN_EPISODIC,
    PROMOTION_TURN_LONG_TERM,
    POLLUTION_GUARD_MIN_LENGTH,
)

# NOTE: MEMORY_* tool definitions are NOT re-exported here on purpose.
# They live in `memory.memory_tools` and importing them transitively
# pulls `tools → supervisor → tools` which causes a circular import.
# Use `from memory.memory_tools import MEMORY_SEARCH` explicitly.

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
    "WorkingMemoryBackend",
    "DictBackend",
    "RedisBackend",
    "RecordDict",
    # Manager
    "MemoryManager",
    "MemoryRouter",
    # Hooks
    "auto_save_memory",
    # Metrics
    "count_working_entries",
    "count_episodic_entries",
    "count_long_term_entries",
    "memory_health_report",
    # Config
    "WORKING_BACKEND",
    "EPISODIC_BACKEND",
    "LONG_TERM_BACKEND",
    "PROMOTION_TURN_EPISODIC",
    "PROMOTION_TURN_LONG_TERM",
    "POLLUTION_GUARD_MIN_LENGTH",
]