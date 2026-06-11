# tools/ — Standalone Tool Definitions

Pre-built `ToolDefinition` instances ready to inject into any `BaseAgent`.

## Directory Structure

```
tools/
├── __init__.py                — Re-exports all tools, maintains backward compatibility
├── _make_tool.py              — ToolDefinition factory (lazy import of agents.base_agent)
├── tool_runner.py             — _call_with_tools() tool-calling loop
├── file/                      — File operation tools
│   ├── __init__.py
│   ├── file_executor.py       — Central security gateway
│   ├── file_create.py
│   ├── file_executor_helpers.py
│   ├── file_grep.py
│   ├── file_info.py
│   ├── file_list.py
│   ├── file_read.py
│   ├── file_read_lines.py
│   ├── file_search.py
│   ├── file_write.py
│   ├── file_op_response.py    — file_op_result() conversational handler
│   ├── file_storage_helpers.py
│   ├── file_storage_service.py
│   └── path_utils.py
├── memory/                    — Memory operation tools
│   ├── __init__.py
│   ├── memory_tools.py        — Core CRUD tools
│   ├── memory_helpers.py      — Shared utilities
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
├── web/                       — Web search tools
│   ├── __init__.py
│   └── web_search.py
├── system/                    — System tools
│   ├── __init__.py
│   ├── calculator.py
│   ├── think.py
│   └── shell_tool.py
├── registry_accessor.py       — Global registry accessor
└── README.md                  — This file
```

## Quick Start

```python
from tools import THINK, CALCULATOR, WEB_SEARCH
from tools import FILE_READ, FILE_WRITE, FILE_CREATE, FILE_LIST, FILE_SEARCH, FILE_GREP
from tools import ALL_TOOLS, FILE_TOOLS, MEMORY_TOOLS

# Individual tools
agent = MyAgent(spec=..., tools=[CALCULATOR, THINK])

# Tool groups
agent = CoderAgent(spec=..., tools=FILE_TOOLS)   # 8 file + web_search + shell
agent = SupervisorAgent(spec=..., tools=MEMORY_TOOLS)  # 16 memory tools (GOAT full-tier)
```

## Architecture (routing + TYPE_CHECKING + Registry)

GOAT 2.0 enforces a strict module-boundary rule:

> `tools/` MUST NOT import from `agents/` or `supervisor/` at module level.
> The cycle tools → agents → tools / tools → supervisor → tools is broken by
> hiding the cross-layer imports inside function bodies.

Three mechanisms together enforce the boundary inside `tools/`:

### 1. `from __future__ import annotations`

Every file in `tools/` starts with this directive. All type hints become
strings, so cross-module classes (`ToolDefinition`, `MemoryManager`,
`TaggedResult`) are looked up lazily and the runtime import that would be
needed to resolve them never runs at import time.

### 2. `if TYPE_CHECKING:` blocks

Cross-module type names are declared under a `TYPE_CHECKING` guard. They
are visible to type checkers (mypy, pyright) but invisible to the runtime
importer — so the cycle is broken even when the type is referenced.

```python
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agents.base_agent import ToolDefinition
    from memory.shared import MemoryManager
    from supervisor.logging.source_types import TaggedResult
```

### 3. Lazy / function-local imports

For values that must be instantiated at runtime (not just hinted at),
`tools/` uses lazy imports inside function bodies:

```python
# tools/system/think.py
THINK = make_tool(
    name="think",
    description="...",
    parameters=_SCHEMA,
    handler=_handler,
)
```

`make_tool` lives in `tools/_make_tool.py` and contains the
`from agents.base_agent import ToolDefinition` import — function-local,
never at module level.

```python
# tools/tool_runner.py
async def _call_with_tools(...):
    # Lazy import — break tools -> supervisor -> tools cycle
    from supervisor.logging.source_types import (
        TaggedResult, TOOL_SOURCE_MAP, infer_source,
    )
    from supervisor.logging.structured_logger import log_tool_call
    ...
```

```python
# tools/file/file_op_response.py
async def file_op_result(...):
    # Lazy imports — the only tools/ file that legitimately touches supervisor/
    from tools import FILE_TOOLS
    from supervisor.types import Plan, SupervisorResult
    from supervisor.behavior.behavior_mirror import mirror_instruction
    ...
```

## Debug Logger Namespace Tree

Every file in `tools/` declares a logger under the `goat2.tools.<submodule>`
namespace. The full tree:

```
goat2.tools                           — tools/__init__.py (top-level)
goat2.tools.make_tool                 — _make_tool.py
goat2.tools.tool_runner               — tool_runner.py
goat2.tools.registry_accessor         — registry_accessor.py

goat2.tools.file                      — tools/file/__init__.py
goat2.tools.file.create               — file_create.py
goat2.tools.file.grep                 — file_grep.py
goat2.tools.file.info                 — file_info.py
goat2.tools.file.list                 — file_list.py
goat2.tools.file.read                 — file_read.py
goat2.tools.file.read_lines           — file_read_lines.py
goat2.tools.file.search               — file_search.py
goat2.tools.file.write                — file_write.py
goat2.tools.file.op_response          — file_op_response.py
goat2.tools.file.executor             — file_executor.py
goat2.tools.file.executor_helpers     — file_executor_helpers.py
goat2.tools.file.storage              — file_storage_service.py
goat2.tools.file.storage_helpers      — file_storage_helpers.py
goat2.tools.file.path_utils           — path_utils.py

goat2.tools.web                       — tools/web/__init__.py
goat2.tools.web.search                — web_search.py

goat2.tools.system                    — tools/system/__init__.py
goat2.tools.system.calculator         — calculator.py
goat2.tools.system.think              — think.py
goat2.tools.system.shell              — shell_tool.py
```

**Log levels:**

- `DEBUG` — tool calls, parameters, results, search hits, dispatch info
- `INFO`  — successful file ops, list/read/write summaries (where they are
  not also produced by the executor)
- `WARNING` — errors, blocked operations, invalid parameters, timeouts

**Enable verbose logging:**

```python
import logging
logging.basicConfig(level=logging.DEBUG)
logging.getLogger("goat2.tools").setLevel(logging.DEBUG)
```

Or per-file:

```python
logging.getLogger("goat2.tools.file.write").setLevel(logging.DEBUG)
```

## Tool Distribution per Agent Role

The tool list per agent role is defined in `supervisor/pipeline/runners.py`
(per-DAG-agent) and `supervisor/identity.py` (GOAT conversational).
This is the canonical, code-level reference; the summary below matches
the actual wiring.

| Agent / Caller | File | Tools | Notes |
|---|---|---|---|
| **GOAT CONVERSATIONAL** | `supervisor/identity.py` | 16 memory tools + `WEB_SEARCH` | No file tools, no shell. Uses `registry.memory_tools`. |
| **file_op_result** (conversational file op) | `tools/file/file_op_response.py` | 10 `FILE_TOOLS` | Routes direct file requests through `tool_caller` model. |
| **DAG tool_caller** | `supervisor/pipeline/runners.py::_run_tool_caller` | 8 file + 4 DAG memory (12 total) | `tool_choice='required'` enforces tool invocation. |
| **DAG researcher** | `supervisor/pipeline/runners.py::_run_researcher` | `WEB_SEARCH, MEMORY_SEARCH_DAG` (2 total) | `tool_choice='required'`. Working tier only. |
| **DAG coder** | `supervisor/pipeline/runners.py::_run_coder` | 8 file + `SHELL` (9 total) | No web_search, no memory. Shell is read-only. |
| **DAG critic** | `supervisor/pipeline/runners.py::_run_critic` | `MEMORY_RECENT_DAG, MEMORY_GET_DAG` (2 total) | Working tier, read-only. |
| **DAG summarizer** | `supervisor/pipeline/runners.py::_run_summarizer` | `MEMORY_RECENT_DAG` (1 total) | Working tier, read-only. |
| **DAG memory** | `supervisor/pipeline/runners.py::_run_memory` | 4 DAG memory (4 total) | Working tier, restricted to `dag:*` namespace. |
| **DAG planner** | (no tools) | — | Pure LLM reasoning. |

**Working tier namespacing:**

- `dag:*` — DAG agents (`MEMORY_*_DAG` tools + `DAG_NAMESPACE`)
- `goat:*` — GOAT conversational (`MEMORY_TOOLS` + `GOAT_NAMESPACE`)
- `validator:*` — GOAT Validator (direct `memory_manager` access, no tool call)
- `promoter:*` — Memory Promoter (direct `memory_manager.promote()`, no tool call)

## Tool Groups

| Export | Contents |
|--------|----------|
| `ALL_TOOLS` | 26 tools (4 system + 8 file + 1 web + 1 shell + 16 memory; minus the 2 system + 2 file + DAG-only memory not in this list — see file) |
| `FILE_TOOLS` | `[FILE_READ, FILE_WRITE, FILE_CREATE, FILE_LIST, FILE_SEARCH, FILE_GREP, FILE_INFO, FILE_READ_LINES, WEB_SEARCH, SHELL]` (10) |
| `MEMORY_TOOLS` | 16 memory tools (GOAT supervisor, full tier) |
| `DAG_MEMORY_TOOLS` | `[MEMORY_SEARCH_DAG, MEMORY_GET_DAG, MEMORY_STORE_DAG, MEMORY_RECENT_DAG]` (4) |

## Tools Overview

### Tool Runner

| Function | File | Description |
|----------|------|-------------|
| `_call_with_tools()` | `tool_runner.py` | Agentic tool-calling loop with retries; lazy-imports `supervisor.logging.*` |

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

## Circular-Import Fixes in tools/

Two pre-existing circular chains have been broken:

1. **`tools/file/file_op_response.py`** — imported
   `supervisor.types.Plan, SupervisorResult` at module level. Fixed by
   moving the import inside `file_op_result()` (lazy) and correcting
   the forward reference from `Registry` (legacy alias) to
   `ServiceRegistry` (the real class).

2. **`tools/tool_runner.py`** — imported
   `supervisor.logging.source_types` and
   `supervisor.logging.structured_logger` at module level, which
   transitively reached `supervisor.registry` → `supervisor.pipeline.runners`
   → back into `tools.tool_runner`. Fixed by moving the imports inside
   the body of `_call_with_tools()` (lazy). `TaggedResult` is still
   referenced in the return-type annotation, but it is a string under
   `from __future__ import annotations` and the real class is only
   resolved at call time.

The companion chain through `supervisor/pipeline/runners.py` (which
imports `from tools.tool_runner import _call_with_tools` at module
level) is the supervisor/ side of the same cycle; it is not modified by
this refactor because the tools/ side is now safe — importing `tools/`
does not pull in `supervisor/`.

## Verification

```python
import sys
import tools
# tools/ import must NOT pull in supervisor/ or agents/ at module level
# (only function-local imports of agents.base_agent.ToolDefinition
#  may be observed, and those happen only on first tool construction)
print("ALL_TOOLS:", len(tools.ALL_TOOLS))   # 26
print("FILE_TOOLS:", len(tools.FILE_TOOLS))  # 10
print("MEMORY_TOOLS:", len(tools.MEMORY_TOOLS))  # 16
print("DAG_MEMORY_TOOLS:", len(tools.DAG_MEMORY_TOOLS))  # 4
```

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
3. Add a module-level logger under `goat2.tools.category.my_tool` namespace
4. Use `make_tool` from `tools._make_tool` to build the `ToolDefinition`
   (this hides the cross-layer `agents.base_agent` import inside a
   function body)
5. Add to `tools/category/__init__.py`
6. Re-export in `tools/__init__.py` and add to `ALL_TOOLS`

```python
"""One-line description of the tool."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from tools._make_tool import make_tool

if TYPE_CHECKING:
    from agents.base_agent import ToolDefinition

log = logging.getLogger("goat2.tools.category.my_tool")

__all__ = ["MY_TOOL"]


async def _handler(param: str) -> str:
    """One-line description of the handler."""
    log.debug("my_tool: param=%r", param)
    return f"result: {param}"


MY_TOOL = make_tool(
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
