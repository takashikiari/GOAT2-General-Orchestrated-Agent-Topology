"""Backward compatibility shim for memory.chromadb_client.

This module has been moved to memory.episodic.chromadb_client.
"""
from __future__ import annotations

import logging

from memory.episodic.chromadb_client import ChromaMemoryClient

log = logging.getLogger("goat2.memory.chroma")

__all__ = ["ChromaMemoryClient"]