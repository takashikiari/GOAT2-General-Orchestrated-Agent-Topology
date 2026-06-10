"""Backward compatibility shim for memory.memory_manager.

This module has been moved to memory.shared.memory_manager.
Import from there or use memory.MemoryManager instead.
"""
from memory.shared.memory_manager import MemoryManager

__all__ = ["MemoryManager"]