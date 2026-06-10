"""Backward compatibility shim for memory.redis_backend.

This module has been moved to memory.working.redis_backend.
"""
from memory.working.redis_backend import RedisBackend

__all__ = ["RedisBackend"]