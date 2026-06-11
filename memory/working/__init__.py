"""Working memory layer — Redis-backed session-scoped storage.

Provides short-term memory with TTL enforcement. Used for session context,
active conversation, tool outputs, and DAG agent communication.

EXPORTS:
- WorkingMemoryLayer: Main session-scoped memory layer
- StorageBackend: Abstract backend interface
- RedisBackend: Redis implementation
- DictBackend: In-memory dictionary implementation
- RecordDict: Serialization wrapper
"""
from __future__ import annotations

import logging

from memory.working.working_memory import WorkingMemoryLayer
from memory.working.working_backend import StorageBackend
from memory.working.redis_backend import RedisBackend
from memory.working.dict_backend import DictBackend
from memory.working.working_record import RecordDict

log = logging.getLogger("goat2.memory.working")

__all__ = [
    "WorkingMemoryLayer",
    "StorageBackend",
    "RedisBackend",
    "DictBackend",
    "RecordDict",
]