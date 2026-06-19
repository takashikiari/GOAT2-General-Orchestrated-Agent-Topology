"""MCP server entry point for GOAT 2.0 debugging.

Run with::

    python -m mcp_server.server

The server uses the stdio transport — Claude Code (or any
MCP client) spawns it as a subprocess and communicates over
stdin/stdout. The server exposes six diagnostic tools
spread across the four ``tools/`` modules.

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
import asyncio
import logging
import sys

from mcp.server import Server
from mcp.server.stdio import stdio_server

log = logging.getLogger("goat2.mcp_server")

__all__ = ["build_server", "main", "run"]


def build_server() -> Server:
    """Construct and configure the MCP ``Server`` instance.

    Imports the four tool modules here (not at module level)
    so an import failure in one tool doesn't prevent the
    others from registering. Each ``register(server)`` call
    is idempotent — multiple invocations are safe.
    """
    server: Server = Server("goat2-debug")

    # Import the tool modules and register them.
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
    return server


def _configure_logging() -> None:
    """Configure root logging once at process start.

    Logs go to stderr so they don't pollute the stdio MCP
    channel. The level is INFO by default; pass ``--verbose``
    on the command line to bump to DEBUG.
    """
    level = logging.INFO
    if "--verbose" in sys.argv:
        level = logging.DEBUG
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


async def run() -> None:
    """Run the MCP server over stdio. Blocks until EOF.

    This is the entry point used by ``python -m mcp_server.server``.
    The ``stdio_server`` context manager owns the asyncio
    streams; ``server.run(read_stream, write_stream, ...)``
    dispatches incoming JSON-RPC requests to the registered
    tool handlers.
    """
    server = build_server()
    log.info("mcp_server: starting stdio server (tools registered)")
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


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
    if args.verbose:
        _configure_logging()
    else:
        _configure_logging()
    asyncio.run(run())


if __name__ == "__main__":
    main()