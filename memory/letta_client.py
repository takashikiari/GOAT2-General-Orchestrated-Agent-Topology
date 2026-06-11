"""Backward compatibility shim for memory.letta_client.

This module has been moved to memory.long_term.letta_client.
"""
from __future__ import annotations

import logging

from memory.long_term.letta_client import LettaClient

log = logging.getLogger("goat2.memory.letta")

__all__ = ["LettaClient"]