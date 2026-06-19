"""MCP server entry point for GOAT 2.0 debugging.

Run with::

    python -m mcp_server.server

The server uses the stdio transport — Claude Code (or any
MCP client) spawns it as a subprocess and communicates over
stdin/stdout. The server exposes six diagnostic tools
spread across the four ``tools/`` modules.

WHY FastMCP (not the low-level ``mcp.server.Server``):
    The four tool modules register handlers via the
    ``@server.tool(...)`` decorator pattern. That decorator
    lives on ``mcp.server.fastmcp.FastMCP`` — the
    high-level, ergonomic wrapper. The low-level
    ``mcp.server.Server`` class has no ``.tool`` attribute;
    using it would silently drop every tool registration
    with ``AttributeError: 'Server' object has no attribute
    'tool'`` at startup. FastMCP also owns the transport
    plumbing (``server.run(transport='stdio')`` is
    one line), so we don't need a manual ``stdio_server``
    context manager.

LAYER NOTES:
    The server is intentionally read-only. It never writes
    to Redis, ChromaDB, Letta, or files. It can run safely
    alongside ``telegram_bot.py`` — both share the same
    backends, and the MCP tools only invoke read methods
    (``list``, ``search``, ``count``, ``health``, ``recall``,
    ``keys``, ``get``, ``read_last_write``, ``tomllib.load``).

REGISTRY POLICY:
    The ``ServiceRegistry`` is created lazily on the first
    tool call (see ``mcp_server._registry``). Importing this
    module does NOT pull in Redis / ChromaDB / Letta clients,
    so the MCP client can spawn the server even when those
    services are down.
"""
from __future__ import annotations

import argparse
import logging

from mcp.server.fastmcp import FastMCP

log = logging.getLogger("goat2.mcp_server")

__all__ = ["build_server", "get_server", "main", "run", "server"]


def _register_all_tools(server: FastMCP) -> None:
    """Wire every diagnostic tool module onto ``server``.

    Imports the tool modules here (not at module level) so
    an import failure in one tool doesn't prevent the others
    from registering. Each ``register(server)`` call is
    idempotent — multiple invocations are safe.
    """
    from mcp_server.tools import (
        diagnose_turn,
        query_config,
        query_logs,
        query_memory,
    )
    for module in (query_logs, query_memory, query_config, diagnose_turn):
        try:
            module.register(server)
            log.debug("mcp_server: registered tools from %s", module.__name__)
        except Exception as exc:  # noqa: BLE001 — don't fail the whole server
            log.warning("mcp_server: %s.register failed: %s", module.__name__, exc)


# Process-wide server instance. Constructed lazily on first
# access so ``import mcp_server.server`` doesn't drag in the
# tool modules (and the GOAT memory stack) until something
# actually needs the server. FastMCP's internal tool list is
# shared with the instance, so this is also the only one
# ``FastMCP`` we'll ever construct in this process — callers
# that need the configured server should use ``get_server()``.
_server: FastMCP | None = None


def build_server() -> FastMCP:
    """Construct a fresh FastMCP and register all tools on it.

    Returns:
        A new ``FastMCP`` named ``"goat2-debug"`` with all
        six diagnostic tools registered. Independent from
        the module-level cached instance returned by
        ``get_server()`` — use this when you need an isolated
        server (e.g. for tests that want a clean tool list).
    """
    server: FastMCP = FastMCP("goat2-debug")
    _register_all_tools(server)
    return server


def get_server() -> FastMCP:
    """Return the process-wide FastMCP, constructing it lazily.

    The first call constructs a ``FastMCP`` and registers
    the six tools. Subsequent calls return the same
    instance so we never double-register. This is the
    instance the MCP SDK talks to when ``run()`` is called.
    """
    global _server
    if _server is None:
        _server = build_server()
    return _server


# Convenience: ``from mcp_server.server import server``.
# Resolved lazily via a module-level ``__getattr__`` so
# importing this module does NOT eagerly build the server.
def __getattr__(name: str):
    if name == "server":
        return get_server()
    raise AttributeError(f"module 'mcp_server.server' has no attribute {name!r}")


def _configure_logging(verbose: bool = False) -> None:
    """Configure root logging once at process start.

    Uses the centralized ``utils.logging.setup.configure_logging``
    so MCP server output lands in the same rotating file
    (``logs/goat2.log``) as the CLI and Telegram bot, plus
    stderr for live diagnostics. stderr is safe here — MCP
    uses stdio (stdout) for the protocol, not stderr.

    Pass ``verbose=True`` to bump the root level to DEBUG.
    """
    from utils.logging.setup import configure_logging
    configure_logging(level="DEBUG" if verbose else "INFO")


def run() -> None:
    """Build the server and start it on stdio. Blocks until EOF.

    FastMCP's ``.run()`` owns the entire transport lifecycle:
    it sets up the asyncio streams, runs the event loop,
    and tears everything down on EOF / SIGINT. There is no
    async context to manage from the caller side.

    This is the entry point used by ``python -m mcp_server.server``.
    """
    server = get_server()
    log.info("mcp_server: starting stdio server (tools registered)")
    server.run(transport="stdio")


def main() -> None:
    """CLI entry point. Supports ``--help`` and ``--verbose``."""
    parser = argparse.ArgumentParser(
        prog="mcp_server.server",
        description=(
            "Read-only MCP diagnostic server for GOAT 2.0. "
            "Speaks MCP over stdio — spawn it from an MCP client "
            "(e.g. Claude Code) and use the registered tools to "
            "inspect GOAT's logs, memory tiers, and per-turn context."
        ),
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable DEBUG-level logging to stderr.",
    )
    args = parser.parse_args()
    _configure_logging(verbose=args.verbose)
    run()


if __name__ == "__main__":
    main()