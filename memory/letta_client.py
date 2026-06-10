"""Backward compatibility shim for memory.letta_client.

This module has been moved to memory.long_term.letta_client.
"""
from memory.long_term.letta_client import LettaClient

__all__ = ["LettaClient"]