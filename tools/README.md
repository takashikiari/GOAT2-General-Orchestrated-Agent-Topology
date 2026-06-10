# tools/ — Standalone Tool Definitions

Pre-built `ToolDefinition` instances ready to inject into any `BaseAgent`.

## Directory Structure

```
tools/
├── __init__.py              — Re-exports all tools, maintains backward compatibility
├── tool_runner.py        — _call_with_tools() tool-calling loop
├── file/                  — File operation tools
│   ├── __init__.py
│   ├── file_executor.py    — Central security gateway
│   ├── file_create.py    — Create new files
│   ├── file_executor_helpers.py
│   ├── file_grep.py      — Search within files
│   ├── file_info.py     — Get file metadata
│   ├── file_list.py      — List directories
│   ├── file_read.py     — Read file contents
│   ├── file_read_lines.py — Read specific lines
│   ├── file_search.py   — Find files by glob
│   ├── file_write.py    — Write file contents
│   ├── file_op_response.py — file_op_result() conversational handler
│   ├── file_storage_helpers.py
│   ├── file_storage_service.py
│   └── path_utils.py
├── memory/                — Memory operation tools
│   ├── __init__.py
│   ├── memory_tools.py       — Core CRUD tools
│   ├── memory_helpers.py   — Shared utilities
│   ├── memory_temporal_tools.py
│   ├── memory_delete_tool.py
│   ├── memory_direct_query.py
│   ├── memory_count_tool.py
│   ├── memory_update_tool.py
│   ├── memory_promote_tool.py
│   ├── memory_auto_promote_tool.py
│   ├── memory_embedding_tool.py
│   ├── memory_export_tool.py
│   ├── memory_last_write.py
│   └── memory_ttl_tool.py
├── web/                   — Web search tools
│   ├── __init__.py
│   └── web_search.py
├── system/                — System tools
│   ├── __init__.py
│   ├── calculator.py
│   ├── think.py
│   └── shell_tool.py
├── registry_accessor.py   — Global registry accessor
└── README.md             — This file
```

## Quick Start

```python
from tools import THINK, CALCULATOR, WEB_SEARCH
from tools import FILE_READ, FILE_WRITE, FILE_CREATE, FILE_LIST, FILE_SEARCH, FILE_GREP
from tools import ALL_TOOLS, FILE_TOOLS, MEMORY_TOOLS

# Individual tools
agent = MyAgent(spec=..., tools=[CALCULATOR, THINK])

# Tool groups
agent = CoderAgent(spec=..., tools=FILE_TOOLS)   # all 9 file tools + web_search
agent = SupervisorAgent(spec=..., tools=MEMORY_TOOLS)  # all memory tools
```

## Tools Overview

### Tool Runner

| Function | File | Description |
|----------|------|-------------|
| `_call_with_tools()` | `tool_runner.py` | Agentic tool-calling loop with retries |

### System Tools

| Tool | File | Description |
|------|------|-------------|
| `THINK` | `system/think.py` | Chain-of-thought reasoning; pure, no I/O |
| `CALCULATOR` | `system/calculator.py` | AST-based math evaluator; +, -, *, /, //, %, ** |
| `SHELL` | `system/shell_tool.py` | Restricted shell commands (DAG agents only) |

### File Tools

| Tool | File | Description |
|------|------|-------------|
| `FILE_READ` | `file/file_read.py` | Read file (up to 1 MB); format-aware JSON/CSV/XML |
| `FILE_WRITE` | `file/file_write.py` | Atomic write via tempfile+os.replace |
| `FILE_CREATE` | `file/file_create.py` | Create new file (fails if exists) |
| `FILE_LIST` | `file/file_list.py` | List directory; 'f'/'d' prefixed |
| `FILE_SEARCH` | `file/file_search.py` | Find files by glob pattern |
| `FILE_GREP` | `file/file_grep.py` | Search within files (case-insensitive) |
| `FILE_INFO` | `file/file_info.py` | File/directory metadata |
| `FILE_READ_LINES` | `file/file_read_lines.py` | Read specific line range (1-indexed) |

### Web Tools

| Tool | File | Description |
|------|------|-------------|
| `WEB_SEARCH` | `web/web_search.py` | Search via local SearXNG instance |

### Memory Tools (GOAT Supervisor)

| Tool | File | Description |
|------|------|-------------|
| `MEMORY_SEARCH` | `memory/memory_tools.py` | Semantic search across tiers |
| `MEMORY_GET` | `memory/memory_tools.py` | Exact-key lookup |
| `MEMORY_STORE` | `memory/memory_tools.py` | Key-value storage |
| `MEMORY_DELETE` | `memory/memory_delete_tool.py` | Delete by key |
| `MEMORY_UPDATE` | `memory/memory_update_tool.py` | Update or upsert |
| `MEMORY_TIMELINE` | `memory/memory_temporal_tools.py` | Time-based query |
| `MEMORY_RECENT` | `memory/memory_temporal_tools.py` | Most recent entries |
| `MEMORY_DEBUG_TRACE` | `memory/memory_temporal_tools.py` | Per-tier debug search |
| `MEMORY_DIRECT_QUERY` | `memory/memory_direct_query.py` | Raw query syntax |
| `MEMORY_LAST_WRITE` | `memory/memory_last_write.py` | Check last write timestamp |
| `MEMORY_COUNT` | `memory/memory_count_tool.py` | Count entries per tier |
| `MEMORY_TTL` | `memory/memory_ttl_tool.py` | Check remaining TTL |
| `MEMORY_EMBEDDING` | `memory/memory_embedding_tool.py` | Get embedding vectors |
| `MEMORY_EXPORT` | `memory/memory_export_tool.py` | Bulk export as JSON |
| `MEMORY_PROMOTE` | `memory/memory_promote_tool.py` | Move between tiers |
| `MEMORY_AUTO_PROMOTE` | `memory/memory_auto_promote_tool.py` | Bulk promotion |

### Memory Tools (DAG Agents - Working Tier Only)

| Tool | File | Description |
|------|------|-------------|
| `MEMORY_SEARCH_DAG` | `memory/memory_tools.py` | Semantic search (working tier) |
| `MEMORY_GET_DAG` | `memory/memory_tools.py` | Exact-key lookup (working tier) |
| `MEMORY_STORE_DAG` | `memory/memory_tools.py` | Key-value storage (working tier) |
| `MEMORY_RECENT_DAG` | `memory/memory_temporal_tools.py` | Recent entries (working tier) |

## Tool Groups

| Export | Contents |
|--------|----------|
| `ALL_TOOLS` | All 26 tools (system + file + web + memory) |
| `FILE_TOOLS` | `[FILE_READ, FILE_WRITE, FILE_CREATE, FILE_LIST, FILE_SEARCH, FILE_GREP, FILE_INFO, FILE_READ_LINES, WEB_SEARCH, SHELL]` |
| `MEMORY_TOOLS` | All 16 memory tools (GOAT supervisor) |
| `DAG_MEMORY_TOOLS` | `[MEMORY_SEARCH_DAG, MEMORY_GET_DAG, MEMORY_STORE_DAG, MEMORY_RECENT_DAG]` |

## File Tool Security (`file/file_executor.py`)

All file tools share `FileToolExecutor`:

- **Workspace root** — `GOAT_WORKSPACE` env var, or project root
- **`~` / `$HOME` expansion** — `os.path.expanduser` + `os.path.expandvars`
- **Symlink escape prevention** — resolved path must be inside workspace
- **Dotdot blocking** — `../../secret` resolves outside workspace is blocked
- **Sensitive file blocking** — `.env`, `id_rsa`, `id_ed25519`, `.pem`, `.key`, `.git`, etc.
- **Size limits** — reads capped at 1 MB; writes at 1 MB
- **Atomic writes** — `file_write` uses `tempfile.NamedTemporaryFile` + `os.replace`
- **`GOAT_ALLOW_OUTSIDE_WORKSPACE=true`** — permit absolute paths outside workspace
- All errors returned as `"ERROR: ..."` strings

## Memory Access Architecture

- **GOAT (supervisor)**: Full tier access with `GOAT_ROLE` from `config.roles`
- **DAG agents**: Working tier only with `SESSION_ROLE` from `config.roles`
- Validation enforced in tool handlers

Tiers:
- `working`: Redis-backed, session-scoped, TTL-enforced
- `episodic`: ChromaDB persistent, semantic search
- `long_term`: Letta core-memory blocks, most persistent

## Configuration Reference

See `config/tools.py` for tool-related constants:

```python
from config.tools import (
    MAX_FILE_SIZE,      # 1 MB default
    MAX_SEARCH_RESULTS, # 100
    SHELL_TIMEOUT,     # 30 seconds
    FILE_ALLOWED_EXTENSIONS,
)
```

## Adding a Tool

1. Create `tools/category/my_tool.py`
2. Write `async def _handler(**kwargs) -> str` — return `"ERROR: ..."` on failure
3. Create module-level `MY_TOOL = ToolDefinition(name, description, parameters, handler)`
4. Add to `tools/category/__init__.py`
5. Re-export in `tools/__init__.py` and add to `ALL_TOOLS`

```python
from agents.base_agent import ToolDefinition

async def _handler(param: str) -> str:
    return f"result: {param}"

MY_TOOL = ToolDefinition(
    name="my_tool",
    description="One-line description.",
    parameters={
        "type": "object",
        "properties": {"param": {"type": "string", "description": "..."}},
        "required": ["param"],
    },
    handler=_handler,
)
```