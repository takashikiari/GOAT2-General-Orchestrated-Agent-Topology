"""Backward compatibility shim for memory.dict_backend.

This module has been moved to memory.working.dict_backend.
"""
from memory.working.dict_backend import DictBackend

__all__ = ["DictBackend"]