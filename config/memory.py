"""Backward compatibility shim for config.memory.

This module has been moved to memory.config.
Import from there instead.
"""
from __future__ import annotations

import logging

log = logging.getLogger("goat2.config.memory")

from memory.config import (
    WORKING_BACKEND,
    EPISODIC_BACKEND,
    LONG_TERM_BACKEND,
    PROMOTION_TURN_EPISODIC,
    PROMOTION_TURN_LONG_TERM,
    POLLUTION_GUARD_MIN_LENGTH,
)

__all__ = [
    "WORKING_BACKEND",
    "EPISODIC_BACKEND",
    "LONG_TERM_BACKEND",
    "PROMOTION_TURN_EPISODIC",
    "PROMOTION_TURN_LONG_TERM",
    "POLLUTION_GUARD_MIN_LENGTH",
]