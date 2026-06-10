"""Backward compatibility shim for memory.validation.

This module has been moved to memory.shared.validation.
"""
from memory.shared.validation import (
    sanitize_content,
    validate_memory_write,
)

__all__ = [
    "sanitize_content",
    "validate_memory_write",
]