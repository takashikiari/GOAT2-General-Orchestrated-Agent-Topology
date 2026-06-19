# GOAT 2.0 — MCP Diagnostic Server

A read-only MCP (Model Context Protocol) server that lets
Claude Code (or any other MCP client) inspect GOAT 2.0's
state mid-conversation. Useful while testing GOAT
conversationally through the Telegram bot.

## What it exposes

Six tools, spread across four modules:

| Tool | Module | Purpose |
|------|--------|---------|
| `get_recent_logs` | `query_logs` | Recent log lines from `logs/goat2.log`, optionally filtered by level. |
| `get_errors` | `query_logs` | ERROR / WARNING / CRITICAL lines from the last N minutes. |
| `get_memory_snapshot` | `query_memory` | Per-tier counts + last-write timestamps + health flags. |
| `get_recent_entries` | `query_memory` | Recent entries from a single tier with full metadata + freshness label. |
| `get_all_config` | `query_config` | All `config/*.toml` files merged into one dict, organized by file. |
| `diagnose_last_turn` | `diagnose_turn` | Reconstruct what GOAT saw on the most recent turn: working-memory block, freshness + namespace labels, included/excluded entries with reasons, GOAT_SYSTEM, LLM response. |

All six are read-only — the server never writes to Redis,
ChromaDB, Letta, or the filesystem. It's safe to run
alongside `telegram_bot.py`.

## Install

The MCP SDK is the only new dependency. From the repo root:

```bash
pip install -r mcp_server/requirements.txt
```

(`mcp>=1.0,<2.0`). All other dependencies are already in
the project's `requirements.txt`.

## Run standalone (for debugging)

The server uses stdio transport, so you usually don't invoke
it manually — your MCP client spawns it for you. But you can
verify the server starts:

```bash
python -m mcp_server.server --help
python -m mcp_server.server --verbose
```

`--verbose` bumps logging to DEBUG (logs go to stderr so
they don't pollute the stdio MCP channel).

## Register with Claude Code

Add the server to your Claude Code MCP config
(`~/.config/claude/mcp.json` on Linux, `~/Library/Application
Support/Claude/mcp.json` on macOS, or the equivalent on
Windows):

```json
{
  "mcpServers": {
    "goat2-debug": {
      "command": "python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/home/lenovo/workspace/goat2"
    }
  }
}
```

Replace the `cwd` with the absolute path to your GOAT 2.0
checkout. Once registered, restart Claude Code and the six
tools will appear in the MCP tool list.

If you use a virtualenv:

```json
{
  "mcpServers": {
    "goat2-debug": {
      "command": "/home/lenovo/workspace/goat2/.venv/bin/python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/home/lenovo/workspace/goat2"
    }
  }
}
```

## Design notes

- **Reuse over duplicate.** `diagnose_turn` imports
  `supervisor.mechanisms.freshness.score_freshness`,
  `supervisor.mechanisms.namespace.classify_namespace`,
  `supervisor.mechanisms.staleness.is_stale`, and
  `supervisor.session.mem_inject.working_memory_block` —
  the diagnosis matches GOAT's actual behavior, not an
  approximation.
- **Lazy ServiceRegistry.** `mcp_server._registry.get_registry()`
  constructs the `ServiceRegistry` on first tool call. The
  server starts even when Redis / ChromaDB / Letta are down;
  individual tools then return graceful errors.
- **Stateless.** No module-level mutable state outside the
  registry cache. Multiple concurrent tool calls are safe.
- **Files ≤ 260 lines.** See per-module docstrings for
  the API surface of each tool.