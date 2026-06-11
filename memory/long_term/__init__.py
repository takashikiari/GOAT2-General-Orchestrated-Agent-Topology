"""Long-term memory layer — Letta API integration.

Provides permanent persistent memory via Letta.
Used for user preferences, profiles, core memories.

EXPORTS:
- LettaClient: Main Letta API client for long-term memory
- LettaHealthProbe: HTTP client + liveness cache
- LettaAgentRegistry: Per-role Letta agent ID cache
- _InContextFallback: Pure in-memory fallback when Letta is down
"""
from __future__ import annotations

import logging

from memory.long_term.letta_client import LettaClient
from memory.long_term.letta_health import LettaHealthProbe
from memory.long_term.letta_registry import LettaAgentRegistry
from memory.long_term.letta_fallback import _InContextFallback

log = logging.getLogger("goat2.memory.letta")

__all__ = [
    "LettaClient",
    "LettaHealthProbe",
    "LettaAgentRegistry",
    "_InContextFallback",
]
