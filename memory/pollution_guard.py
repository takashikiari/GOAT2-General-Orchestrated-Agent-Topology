"""Backward compatibility shim for memory.pollution_guard.

This module has been moved to memory.shared.pollution_guard.
"""
from memory.shared.pollution_guard import PollutionGuard

__all__ = ["PollutionGuard"]