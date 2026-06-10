"""Backward compatibility shim for memory.chromadb_client.

This module has been moved to memory.episodic.chromadb_client.
"""
from memory.episodic.chromadb_client import ChromaMemoryClient

__all__ = ["ChromaMemoryClient"]