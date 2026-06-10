"""Backward compatibility shim for memory.hooks.

This module has been moved to memory.shared.hooks.
"""
from memory.shared.hooks import auto_save_memory

__all__ = ["auto_save_memory"]