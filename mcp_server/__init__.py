"""mcp_server — read-only MCP diagnostic layer for GOAT 2.0.

Exposes a small set of tools that Claude Code (or any MCP
client) can use to inspect GOAT's state mid-conversation:

  - query_logs       — recent log lines + error/warning filter
  - query_memory     — memory-tier snapshots + recent entries
  - query_config     — merged view of all config/*.toml files
  - diagnose_turn    — reconstruct what GOAT actually saw on
                       the most recent turn (reusing the same
                       freshness/namespace/mem_inject functions
                       GOAT itself uses)

DESIGN PRINCIPLES:
  - READ-ONLY. No tool writes to Redis, ChromaDB, Letta, or
    files. The MCP server is safe to run concurrently with
    the Telegram bot — both share the same backends and
    neither holds locks the other needs.
  - Reuse over duplicate. ``diagnose_turn`` imports the
    same mechanisms GOAT uses, so the diagnosis matches
    reality exactly instead of being an approximation.
  - Stateless. No module-level state. Each tool call
    constructs a fresh ``ServiceRegistry`` (lazy) and
    discards it on return.
"""
from __future__ import annotations

__all__: list[str] = []
