"""Lazily-constructed ``ServiceRegistry`` accessor for the MCP
server. Lives in its own tiny module so the four tool modules
can share a single import line.

The accessor is stateless: each call returns the same
``ServiceRegistry`` instance (so Redis / ChromaDB / Letta
connection pools are reused across MCP tool calls), but no
module-level ``ServiceRegistry()`` is constructed at import
time — that would force the MCP client to drag in the whole
memory stack just to import this package.

CONCURRENCY:
    The accessor holds one ``ServiceRegistry`` per process.
    The underlying backends (Redis, ChromaDB, Letta) are
    connection-pooled and safe to read concurrently with
    ``telegram_bot.py`` running. The MCP server never calls
    any write method on those backends — see the tools
    themselves for the read-only contract.
"""
from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config.registry import ServiceRegistry

log = logging.getLogger("goat2.mcp_server")

__all__ = ["get_registry"]

_lock = threading.Lock()
_registry: "ServiceRegistry | None" = None


def get_registry() -> "ServiceRegistry":
    """Return the process-wide ServiceRegistry, building it lazily.

    The first call instantiates ``ServiceRegistry()`` (which
    in turn constructs Redis / ChromaDB / Letta clients).
    Subsequent calls reuse the cached instance so connection
    pools are not re-created on every tool call.

    Returns:
        The singleton-per-process ``ServiceRegistry``.

    Raises:
        RuntimeError: when the registry cannot be constructed.
        The original exception is wrapped so callers can
        decide whether to surface it as an MCP error or fall
        back gracefully.
    """
    global _registry
    if _registry is not None:
        return _registry
    with _lock:
        if _registry is not None:
            return _registry
        try:
            from config.registry import ServiceRegistry
            _registry = ServiceRegistry()
            log.debug("mcp_server: ServiceRegistry ready")
            return _registry
        except Exception as exc:  # noqa: BLE001 — convert to a clearer error
            raise RuntimeError(
                f"mcp_server: cannot construct ServiceRegistry: {exc}"
            ) from exc