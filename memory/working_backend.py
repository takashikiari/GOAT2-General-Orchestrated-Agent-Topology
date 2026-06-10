"""Backward compatibility shim for memory.working_backend.

This module has been moved to memory.working.working_backend.
"""
from memory.working.working_backend import StorageBackend

__all__ = ["StorageBackend"]