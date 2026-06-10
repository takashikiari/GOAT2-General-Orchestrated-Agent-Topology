"""Backward compatibility shim for memory.working_memory.

This module has been moved to memory.working.working_memory.
"""
from memory.working.working_memory import WorkingMemoryLayer

__all__ = ["WorkingMemoryLayer"]