# GOAT 2.0 — Personal Assistant with Persistent Memory

GOAT is a multi-agent personal assistant with three-tier memory (working, episodic, long-term),
intelligent intent routing, and a full tool-calling system for file operations, web search,
memory queries, and code execution.

---

## Architecture

### GOAT Supervisor vs DAG Agents

**GOAT Supervisor:**
- Manages memory read/write directly across all three tiers (Redis, ChromaDB, Letta)
- Uses `role="goat"` for memory operations
- Validates task success by checking tool parameters — never reports validated without verification
- Orchestrates DAG execution, critique, and synthesis

**DAG Agents (planner, researcher, coder, critic, summarizer, tool_caller):**
- Access tools but restricted to working memory (Redis) only
- Use `role="user_session"` and `tier="working"` for all memory operations
- No direct access to ChromaDB (episodic) or Letta (long-term)
- Tool parameters validated by GOAT before marking tasks successful

---

## Tools module — `tools/`

The `tools/` package provides standalone `ToolDefinition` instances ready to inject into
any agent. All tools follow the same contract:

- **Async handler** — `async def _handler(**kwargs) -> str`
- **Error convention** — return `"ERROR: <reason>"` on failure; never raise
- **Docstring** — each file has a module-level docstring describing its purpose

### Tool inventory (26 tools)

| Tool | File | Purpose | Access |
|------|------|---------|--------|
| `THINK` | `think.py` | Chain-of-thought scratchpad | all |
| `CALCULATOR` | `calculator.py` | AST-based safe arithmetic | all |
| `WEB_SEARCH` | `web_search.py` | DuckDuckGo / custom backend | all |
| `FILE_READ` | `file_read.py` | Read file; chunked, format-aware (JSON/CSV/XML) | all |
| `FILE_WRITE` | `file_write.py` | Atomic write; overwrite or append mode | all |
| `FILE_CREATE` | `file_create.py` | Create file; fails if exists unless exist_ok | all |
| `FILE_LIST` | `file_list.py` | List directory entries with sizes | all |
| `FILE_SEARCH` | `file_search.py` | Glob pattern search for files | all |
| `FILE_GREP` | `file_grep.py` | Search pattern within a file; returns numbered lines | all |
| `FILE_INFO` | `file_info.py` | File/directory metadata (size, timestamps, permissions) | all |
| `FILE_READ_LINES` | `file_read_lines.py` | Read a specific line range (1-indexed) | all |
| `SHELL` | `shell_tool.py` | Basic read-only shell commands (ls, pwd, cat, grep, etc.) | DAG only |
| `MEMORY_SEARCH` | `memory_tools.py` | Semantic search across tiers | GOAT: all tiers / DAG: working |
| `MEMORY_GET` | `memory_tools.py` | Exact-key lookup | GOAT: all tiers / DAG: working |
| `MEMORY_STORE` | `memory_tools.py` | Write to specified tier | GOAT: all tiers / DAG: working |
| `MEMORY_DELETE` | `memory_delete_tool.py` | Delete entry by key | GOAT only |
| `MEMORY_UPDATE` | `memory_update_tool.py` | Update existing entry | GOAT only |
| `MEMORY_TIMELINE` | `memory_temporal_tools.py` | Entries in time range | GOAT only |
| `MEMORY_RECENT` | `memory_temporal_tools.py` | Most recent entries | GOAT: all tiers / DAG: working |
| `MEMORY_DEBUG_TRACE` | `memory_temporal_tools.py` | Per-tier debug JSON | GOAT only |
| `MEMORY_DIRECT_QUERY` | `memory_direct_query.py` | Raw queries to Letta/ChromaDB/Redis | GOAT only |
| `MEMORY_LAST_WRITE` | `memory_last_write.py` | Check last-write timestamp | GOAT only |
| `MEMORY_COUNT` | `memory_count_tool.py` | Count entries in tier | GOAT only |
| `MEMORY_TTL` | `memory_ttl_tool.py` | Get/set TTL for entries | GOAT only |
| `MEMORY_EMBEDDING` | `memory_embedding_tool.py` | Get embedding vector | GOAT only |
| `MEMORY_EXPORT` | `memory_export_tool.py` | Export tier entries | GOAT only |
| `MEMORY_PROMOTE` | `memory_promote_tool.py` | Promote entry between tiers | GOAT only |
| `MEMORY_AUTO_PROMOTE` | `memory_auto_promote_tool.py` | Auto-promote based on TTL | GOAT only |

### Convenience groups

| Export | Contents |
|--------|----------|
| `ALL_TOOLS` | All 26 tools |
| `FILE_TOOLS` | `FILE_READ`, `FILE_WRITE`, `FILE_CREATE`, `FILE_LIST`, `FILE_SEARCH`, `FILE_GREP`, `FILE_INFO`, `FILE_READ_LINES`, `WEB_SEARCH`, `SHELL` |
| `MEMORY_TOOLS` | All 16 GOAT memory tools (full tier access) |
| `DAG_MEMORY_TOOLS` | 4 memory tools for DAG agents (working tier only): `MEMORY_SEARCH`, `MEMORY_GET`, `MEMORY_STORE`, `MEMORY_RECENT` |

### Security

All file tools delegate to `FileToolExecutor` (`tools/file_executor.py`):

- Workspace root: `GOAT_WORKSPACE` env var or project root
- Blocks: dotdot traversal, symlink escape, sensitive files (`.env`, `id_rsa`, `.pem`, etc.)
- Atomic writes via `tempfile.NamedTemporaryFile` + `os.replace`
- `GOAT_ALLOW_OUTSIDE_WORKSPACE=true` + `GOAT_ALLOWED_PATHS` allowlist

---

## Memory Tool Binding — GOAT vs DAG Separation

Memory tool access is strictly separated between GOAT (supervisor) and DAG (agent pipeline):

### GOAT (supervisor/assistant)
- **Full access** to all three memory backends: **Redis** (working), **ChromaDB** (episodic), **Letta** (long-term)
- Uses `MEMORY_TOOLS` (16 tools) with full tier access
- Memory tools have `tier` parameter accepting `any`, `working`, `episodic`, `long_term`
- Reads recent turns, session context, user profile directly — no tool calls needed
- Validates task success by checking tool parameters

### DAG (agents — planner, researcher, coder, critic, summarizer, tool_caller)
- **Redis read/write only** — DAG agents access **working** memory tier only
- **No access** to ChromaDB (episodic) or Letta (long-term)
- Uses `DAG_MEMORY_TOOLS` (4 tools) - **no `tier` parameter**:
  - `memory_search` - search working memory only
  - `memory_get` - get from working memory only
  - `memory_store` - store to working memory only
  - `memory_recent` - recent working memory entries only
- System prompt explicitly states: "Memory (working tier only): memory_search, memory_get, memory_store, memory_recent"
- Tool parameters validated by GOAT before marking tasks successful

### Implementation
- `tools/memory_tools.py`: GOAT handlers use `GOAT_ROLE`; DAG handlers use `SESSION_ROLE` and force `tier="working"`
- `tools/memory_temporal_tools.py`: GOAT handler (`_recent_handler`) has `tier` param; DAG handler (`_recent_handler_dag`) forces `tier="working"`
- `supervisor/session.py`: `store_turn()` writes to WORKING tier (Redis) only with `role="user_session"`
- `supervisor/supervisor.py`: `finalize_session()` may promote turns from WORKING to EPISODIC/LONG_TERM
- `supervisor/dag_validator.py`: Validates tool_called, tool_name, raw_output_hash before marking safe

---

## Source provenance and trust layer

Every tool call is tagged with a data source: **net** (web search), **memory** (recall),
**file** (filesystem), or **generated** (pure LLM output). This tag flows from the tool
call through the DAG to the final `SupervisorResult.sources` dict.

### Validation rules

GOAT supervisor validates task success by checking:
- `tool_called` is True
- `tool_name` is non-empty
- `raw_output_hash` is non-empty (proves tool execution)

If any parameter is missing, task is marked `validated=False` and synthesis is skipped.

| Role | Allowed sources | Enforced by |
|------|----------------|-------------|
| `researcher` | `net` only | runner raises + dag_validator |
| `memory` | `memory` only | dag_validator (UNVERIFIED) |
| `coder` | `file`, `net`, `memory`, `generated` | dag_validator (source_violation if outside set) |
| `tool_caller` | `file`, `net`, `memory`, `generated` | runner raises for search tasks |
| `critic` / `summarizer` | `generated`, `file`, `memory` | dag_validator |
| `planner` | `generated` | dag_validator |

**`[require_source: true]`** is prepended to every DAG plan context. If any task returns
without a source tag or missing tool parameters, the supervisor responds with `"Unverified"`
instead of forwarding the synthesized answer.

---

See `CHANGELOG.md` for full history and `SESSION_NOTES.md` for current session notes.
