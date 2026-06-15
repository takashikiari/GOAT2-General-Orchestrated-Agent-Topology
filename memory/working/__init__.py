"""Working memory layer — Redis-backed session-scoped storage.

Provides short-term memory with TTL enforcement. Used for session context,
active conversation, tool outputs, and DAG agent communication.

EXPORTS:
- WorkingMemoryLayer: Main session-scoped memory layer
- WorkingMemoryBackend: Storage-neutral backend Protocol (swap any backend)
- StorageBackend: Legacy backend interface (kept for backward compatibility)
- RedisBackend: Networked key-value implementation
- DictBackend: In-memory dictionary implementation
- RecordDict: Serialization wrapper
- check_and_promote: LLM-scored promotion when working memory fills up
"""
from __future__ import annotations

import logging

from memory.working.working_memory import WorkingMemoryLayer
from memory.working.backend_protocol import WorkingMemoryBackend
from memory.working.working_backend import StorageBackend
from memory.working.redis_backend import RedisBackend
from memory.working.dict_backend import DictBackend
from memory.working.working_record import RecordDict
from memory.working.capacity import check_and_promote

log = logging.getLogger("goat2.memory.working")

__all__ = [
    "WorkingMemoryLayer",
    "WorkingMemoryBackend",
    "StorageBackend",
    "RedisBackend",
    "DictBackend",
    "RecordDict",
    "check_and_promote",
]