"""Long-term memory layer — Letta API integration.

Provides permanent persistent memory via Letta.
Used for user preferences, profiles, core memories.

EXPORTS:
- LettaClient: Main Letta API client for long-term memory
"""
from memory.long_term.letta_client import LettaClient

__all__ = [
    "LettaClient",
]