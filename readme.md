# GOAT 2.0 — Personal Assistant with Persistent Memory

GOAT is a multi-agent personal assistant with three-tier memory (working, episodic, long-term),
intelligent intent routing, and a full tool-calling system for file operations, web search,
memory queries, and code execution.

---

## Tools module — `tools/`

The `tools/` package provides standalone `ToolDefinition` instances ready to inject into
any agent. All tools follow the same contract:

- **Async handler** — `async def _handler(**kwargs) -> str`
- **Error convention** — return `"ERROR: <reason>"` on failure; never raise
- **Docstring** — each file has a module-level docstring describing its purpose

### Tool inventory (17 tools)

| Tool | File | Purpose |
|------|------|---------|
| `THINK` | `think.py` | Chain-of-thought scratchpad |
| `CALCULATOR` | `calculator.py` | AST-based safe arithmetic |
| `WEB_SEARCH` | `web_search.py` | DuckDuckGo / custom backend |
| `FILE_READ` | `file_read.py` | Read file; chunked, format-aware (JSON/CSV/XML) |
| `FILE_WRITE` | `file_write.py` | Atomic write; overwrite or append mode |
| `FILE_CREATE` | `file_create.py` | Create file; fails if exists unless exist_ok |
| `FILE_LIST` | `file_list.py` | List directory entries with sizes |
| `FILE_SEARCH` | `file_search.py` | Glob pattern search for files |
| `FILE_GREP` | `file_grep.py` | Search pattern within a file; returns numbered lines |
| `FILE_INFO` | `file_info.py` | File/directory metadata (size, timestamps, permissions) |
| `FILE_READ_LINES` | `file_read_lines.py` | Read a specific line range (1-indexed) |
| `MEMORY_SEARCH` | `memory_tools.py` | Semantic fan-out with optional time window |
| `MEMORY_GET` | `memory_tools.py` | Exact-key lookup across tiers |
| `MEMORY_STORE` | `memory_tools.py` | Write to specified tier |
| `MEMORY_TIMELINE` | `memory_temporal_tools.py` | Entries in time range |
| `MEMORY_RECENT` | `memory_temporal_tools.py` | Most recent entries |
| `MEMORY_DEBUG_TRACE` | `memory_temporal_tools.py` | Per-tier debug JSON |

### Fixes applied to tools

1. **Docstrings added** — every tool file now has a module-level docstring in English
   describing its purpose and functionality. Files modified: `tools/__init__.py`,
   `tools/calculator.py`, `tools/memory_temporal_tools.py`, `tools/memory_tools.py`,
   `tools/think.py`, `tools/web_search.py`. No other code was changed.

2. **`file_storage_service.py` refactored** — rewritten to under 200 lines by importing
   shared helpers from `file_storage_helpers.py`. The abstract `FileStorageService` base
   class and its concrete implementations (`LocalFileStorage`, `S3FileStorage`) now
   delegate path resolution, error handling, and factory logic to the helpers module.

### Convenience groups

| Export | Contents |
|--------|----------|
| `ALL_TOOLS` | All 17 tools |
| `FILE_TOOLS` | `FILE_READ`, `FILE_WRITE`, `FILE_CREATE`, `FILE_LIST`, `FILE_SEARCH`, `FILE_GREP`, `FILE_INFO`, `FILE_READ_LINES`, `WEB_SEARCH` |
| `MEMORY_TOOLS` | `MEMORY_SEARCH`, `MEMORY_GET`, `MEMORY_STORE`, `MEMORY_TIMELINE`, `MEMORY_RECENT`, `MEMORY_DEBUG_TRACE` |

### Security

All file tools delegate to `FileToolExecutor` (`tools/file_executor.py`):

- Workspace root: `GOAT_WORKSPACE` env var or project root
- Blocks: dotdot traversal, symlink escape, sensitive files (`.env`, `id_rsa`, `.pem`, etc.)
- Atomic writes via `tempfile.NamedTemporaryFile` + `os.replace`
- `GOAT_ALLOW_OUTSIDE_WORKSPACE=true` + `GOAT_ALLOWED_PATHS` allowlist

---

## Architecture overview

```
User input → CLI / Telegram
                ↓
         GoatSupervisor.run()
                ↓
         Intent classifier
           ├─ CONVERSATIONAL → direct_response (LLM + CORE_TOOLS)
           ├─ ANALYTICAL     → planner → tool_caller → synthesize
           └─ COMPLEX        → planner → wave execution → critique → synthesize
                ↓
         Memory: 3 tiers (working / episodic / long-term)
         Tools:  file ops, web search, memory CRUD, calculator, think
```

---

## Source provenance and trust layer — `supervisor/source_types.py` et al.

Every tool call is tagged with a data source: **net** (web search), **memory** (recall),
**file** (filesystem), or **generated** (pure LLM output). This tag flows from the tool
call through the DAG to the final `SupervisorResult.sources` dict.

### Source enforcement rules (patches 52–54)

Execution tasks must call a real tool — `source='generated'` is blocked:

| Role | Allowed sources | Enforced by |
|------|----------------|-------------|
| `researcher` | `net` only | runner raises + dag_validator |
| `memory` | `memory` only | dag_validator (UNVERIFIED) |
| `coder` | `file`, `net`, `memory`, `generated` | dag_validator (source_violation if outside set) |
| `tool_caller` | `file`, `net`, `memory`, `generated` | runner raises for search tasks |
| `critic` / `summarizer` | `generated`, `file`, `memory` | dag_validator |
| `planner` | `generated` | dag_validator |

`AgentResult` now carries `tool_called: bool`, `tool_name: str`, `raw_output_hash: str`.
If any DAG node fails source validation, GOAT responds with a factual failure message
(e.g. `"Not available. researcher via web_search: web search returned an error."`) and
logs task IDs + reasons before blocking synthesis.
If synthesis succeeds but returns empty output, GOAT constructs a factual fallback listing
which tools were called — no LLM re-call, no generated content.
`_run_summarizer` skips the LLM entirely when all upstream task outputs are empty.
`GOAT_SYSTEM` and the synthesis prompt both include `"No apologies."` so apologies are
never used in place of factual unavailability statements.

| Component | File | Role |
|-----------|------|------|
| `SourceTag` / `TaggedResult` | `source_types.py` | Type definitions + `infer_source` |
| Structured log per call | `structured_logger.py` | JSON to `goat2.tool_calls.structured` |
| Post-execution validator | `dag_validator.py` | Whitelist + UNVERIFIED + empty_file_read + net errors + stale memory |
| Outbound content filter | `interfaces/content_filter.py` | Masks API keys / secrets before sending to Telegram |
| Cross-tool auditor | `auditor.py` | Jaccard similarity anomaly detection |

**`[require_source: true]`** is prepended to every DAG plan context. If any task returns
without a source tag, the supervisor responds with `"Unverified"` instead of forwarding
the synthesized answer.

---

See `CHANGELOG.md` for full history and `SESSION_NOTES.md` for current session notes.
