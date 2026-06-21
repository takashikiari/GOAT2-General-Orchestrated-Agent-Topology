"""
memory.episodic — cross-session semantic memory backed by ChromaDB.

Re-exports EpisodicMemory for convenient top-level import:
    from memory.episodic import EpisodicMemory
"""
from memory.episodic.episodic import EpisodicMemory

__all__ = ["EpisodicMemory"]
