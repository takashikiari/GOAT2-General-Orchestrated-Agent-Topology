# tools/ — Standalone Tool Definitions

Pre-built `ToolDefinition` instances ready to inject into any `BaseAgent`.

```python
from tools import THINK, CALCULATOR, WEB_SEARCH
from tools import FILE_READ, FILE_WRITE, FILE_CREATE, FILE_LIST, FILE_SEARCH, FILE_GREP, FILE_INFO, FILE_READ_LINES
from tools import ALL_TOOLS, FILE_TOOLS, MEMORY_TOOLS

agent = MyAgent(spec=..., tools=[CALCULATOR, THINK])
agent = CoderAgent(spec=..., tools=FILE_TOOLS)   # all 9 file tools + web_search
```

## Tools (17 total)

| Tool | File | Signature | Notes |
|------|------|-----------|-------|
| `THINK` | `think.py` | `think(thought)` | Chain-of-thought scratchpad; pure, no I/O |
| `CALCULATOR` | `calculator.py` | `calculator(expression)` | AST-based, no `eval()`, exponents capped at 1000 |
| `WEB_SEARCH` | `web_search.py` | `web_search(query, num_results=5)` | DuckDuckGo; override via `SEARCH_API_URL` |
| `FILE_READ` | `file_read.py` | `file_read(path, offset=0, limit=MAX, format_aware=True)` | Read file; UTF-8, 1 MB limit; chunked reads; format-aware JSON/CSV/XML parsing |
| `FILE_WRITE` | `file_write.py` | `file_write(path, content, mode="overwrite")` | Atomic write (tempfile+os.replace); creates files; supports append mode |
| `FILE_CREATE` | `file_create.py` | `file_create(path, content="", exist_ok=False)` | Create new file; fails if exists unless exist_ok=true |
| `FILE_LIST` | `file_list.py` | `file_list(path, limit=200)` | List directory; 'f'/'d' prefixed entries with sizes |
| `FILE_SEARCH` | `file_search.py` | `file_search(pattern, path=".", limit=100)` | Find files by glob pattern; returns relative paths |
| `FILE_GREP` | `file_grep.py` | `file_grep(path, pattern, max_results=50)` | Case-insensitive substring search within a file; returns matching lines with numbers |
| `FILE_INFO` | `file_info.py` | `file_info(path)` | File/dir metadata: name, size, timestamps, permissions, entry count |
| `FILE_READ_LINES` | `file_read_lines.py` | `file_read_lines(path, start_line=1, end_line=None)` | Read a specific line range (1-indexed); returns numbered lines |
| `MEMORY_SEARCH` | `memory_tools.py` | `memory_search(query, limit=20, start_datetime=None, end_datetime=None, tier="any")` | Semantic fan-out with optional time window; ISO 8601 or natural language |
| `MEMORY_GET` | `memory_tools.py` | `memory_get(key, tier="any")` | Exact-key lookup; tier="any" probes WORKING→EPISODIC→LONG_TERM |
| `MEMORY_STORE` | `memory_tools.py` | `memory_store(key, value, tier="working")` | Write to the specified tier; validates tier |
| `MEMORY_TIMELINE` | `memory_temporal_tools.py` | `memory_timeline(start_datetime, end_datetime, tier="any", limit=100)` | All entries in a time window, newest first |
| `MEMORY_RECENT` | `memory_temporal_tools.py` | `memory_recent(limit=50, tier="any")` | Most recent N entries |
| `MEMORY_DEBUG_TRACE` | `memory_temporal_tools.py` | `memory_debug_trace(query, start_datetime=None, end_datetime=None)` | Per-tier search with match counts; JSON output |

## Convenience groups

| Export | Contents |
|--------|----------|
| `ALL_TOOLS` | All 17 tools |
| `FILE_TOOLS` | `[FILE_READ, FILE_WRITE, FILE_CREATE, FILE_LIST, FILE_SEARCH, FILE_GREP, FILE_INFO, FILE_READ_LINES, WEB_SEARCH]` |
| `MEMORY_TOOLS` | `[MEMORY_SEARCH, MEMORY_GET, MEMORY_STORE, MEMORY_TIMELINE, MEMORY_RECENT, MEMORY_DEBUG_TRACE]` |

## File tool path safety (`tools/file_executor.py`)

All file tools share `FileToolExecutor` from `tools/file_executor.py`:

- **Workspace root** — `GOAT_WORKSPACE` env var, or the project root (`tools/../`) by default.
- **`~` / `$HOME` expansion** — `os.path.expanduser` + `os.path.expandvars` run before resolution.
- **Symlink escape** — `Path.resolve()` follows all symlinks; resolved path must be inside workspace.
- **Dotdot traversal** — `../../secret` resolves outside workspace and is blocked.
- **Sensitive file blocking** — `.env`, `id_rsa`, `id_ed25519`, `.pem`, `.key`, `.p12`, `.pfx`,
  `.git/`, `__pycache__/`, `secrets/`, `.ssh/` are denied on read and write.
- **Size limits** — reads capped at `FILE_READ_MAX_BYTES` (default 1 MB); writes at
  `FILE_WRITE_MAX_BYTES` (default 1 MB).
- **Atomic writes** — `file_write` uses `tempfile.NamedTemporaryFile` + `os.replace` for
  crash-safe updates. No partial files on failure.
- **`GOAT_ALLOW_OUTSIDE_WORKSPACE=true`** — permit absolute paths outside workspace.
  Combine with `GOAT_ALLOWED_PATHS=/path1:/path2` to restrict which outside paths are allowed.
- All errors returned as `"ERROR: ..."` strings — handlers never raise.

### Tool semantics

| Tool | File must exist? | Creates parents? | Overwrites? |
|------|-----------------|-----------------|-------------|
| `file_read` | yes | — | — |
| `file_write` | no (creates) | yes | always (atomic) |
| `file_create` | no | yes | only when `exist_ok=true` |
| `file_list` | dir must exist | — | — |

`file_write` creates files atomically via tempfile + os.replace. `file_create` fails on
existing files unless `exist_ok=true` — useful when the agent must not silently overwrite.

## Adding a tool

1. Create `tools/my_tool.py` (≤90 lines).
2. Write `async def _handler(**kwargs) -> str` — return `"ERROR: ..."` on failure.
3. Create module-level `MY_TOOL = ToolDefinition(name, description, parameters, handler)`.
4. Add to `tools/__init__.py` and `ALL_TOOLS`.

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
