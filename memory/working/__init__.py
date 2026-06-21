"""
memory.working — session-scoped working memory backed by Redis.

Re-exports WorkingMemory for convenient top-level import:
    from memory.working import WorkingMemory
"""
from memory.working.working import WorkingMemory

__all__ = ["WorkingMemory"]
