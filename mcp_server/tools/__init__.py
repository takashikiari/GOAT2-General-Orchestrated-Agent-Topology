"""mcp_server.tools — tool implementations for the GOAT 2.0
MCP diagnostic server.

Five modules, each owning one or more MCP tools:

  - ``query_logs``     — log-file inspectors
  - ``query_memory``   — memory-tier inspectors
  - ``query_config``   — toml configuration reader
  - ``diagnose_turn``  — per-turn LLM-context reconstructor
  - ``query_state``    — higher-level state queries (search_logs,
                         get_session_trace, get_supervisor_state,
                         get_memory_entry)

Each module exposes:
  - one or more plain async functions (the tool handlers)
  - a ``register(server)`` function that wires the tools
    onto an ``mcp.server.Server`` instance

The plain functions are the testable surface; the
``register`` adapters are the surface the MCP SDK sees.
"""
from __future__ import annotations

from mcp_server.tools import (
    diagnose_turn,
    query_config,
    query_logs,
    query_memory,
    query_state,
)

__all__ = [
    "diagnose_turn",
    "query_config",
    "query_logs",
    "query_memory",
    "query_state",
]
