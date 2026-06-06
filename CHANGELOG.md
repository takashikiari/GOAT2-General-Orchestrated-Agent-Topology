# Changelog

All notable changes to GOAT 2.0 are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased] — 2026-06-06 (patch 55)

### Fixed

#### Memory recall returning empty results — three-layer fix

**Root causes:**
1. `supervisor/runner_memory.py` Tier 2 used the wrong Letta endpoint
   (`/v1/archival-memory/search`, global, 404) instead of the agent-scoped path.
   It also used the wrong query param (`query`) and parsed a non-existent response key
   (`memories`).
2. `memory/letta_registry.py` `_find_or_create` accepted `agents[0]` without verifying
   the name — when Letta ignores the `name` filter param and returns all agents, the first
   result could be a different agent (e.g., `goat2-user_session`), causing all memory
   queries to hit an empty unrelated agent.
3. `memory/letta_ops_retrieve.py` had no debug logging, making it impossible to see what
   keyword was sent and what Letta returned.

**`supervisor/runner_memory.py`**:
- Added module docstring.
- Tier 2 completely rewritten:
  - `AsyncClient` now uses `base_url=letta.base_url` so relative paths work.
  - Resolves `goat2-memory` agent ID dynamically: `GET /v1/agents/?name=goat2-memory&limit=5`,
    then `next((a for a in agents if a.get("name") == "goat2-memory"), None)` for exact match.
  - Uses correct path `GET /v1/agents/{agent_id}/archival-memory` with `search=kw` param.
  - Same keyword extraction logic as `do_search` (longest word > 3 chars).
  - Parses `results` / `passages` response keys (matching `_extract_passages` logic).
  - `log.debug` lines show agent_id, keyword, HTTP status, and truncated response body.

**`memory/letta_registry.py`**:
- Added module docstring; added missing docstrings to `__init__`, `_get_lock`, `get_agent_id`,
  `_find_or_create`.
- `_find_or_create`: changed `limit=1` → `limit=5`; replaced `agents[0]` acceptance with
  `next((a for a in agents if a.get("name") == name), None)` — exact name match required.
  If no match, logs debug and falls through to `_create`.

**`memory/letta_ops_retrieve.py`**:
- Added module docstring; added docstring to `do_retrieve`.
- `do_search`: added `log.debug` before the HTTP call (agent_id, kw, limit) and after
  (HTTP status, first 300 chars of response body). Enables diagnosis when Letta returns
  empty or unexpected responses.

All 37 existing tests pass. All modified files ≤200 lines with docstrings on every function.

---

## [Unreleased] — 2026-06-06 (patch 54)

### Fixed

#### P1: Empty output after file tool call (tool_runner.py — already applied, confirmed)
`_call_with_tools` no-tool-calls return path already falls back to the last `role=tool`
history entry when `msg.content` is empty. No code change needed — confirmed present.

#### P2: GOAT hallucinates when it lacks facts

**`supervisor/supervisor.py`**:
- `_unverified_summary` now includes the tool that was called (when available) in each
  failure line: `"researcher via web_search: web search returned an error"` instead of
  `"researcher: web search returned an error"`. Uses `AgentResult.tool_name`.
- After `synthesize_results`, if the returned summary is empty or whitespace, GOAT now
  sets `summary` to a factual fallback listing the tools that were called:
  `"Not available. Tools called: {tools}. No output from synthesis."` — no LLM call.
  Prevents silent empty responses reaching the interface.

**`supervisor/runners.py`**:
- `_run_summarizer`: added pre-check — if all upstream `dep_results` have empty outputs,
  the LLM is never called. Returns `"Not available. Upstream tasks returned no output."`
  immediately. Removes the fallback that previously called the LLM with empty context
  and could generate plausible-sounding but unverified content.

#### P3: Supervisor response discipline — explicit at all times

**`supervisor/identity.py`**:
- `GOAT_SYSTEM`: added `"no apologies"` to the no-filler rule so the constraint is
  unambiguous: `"No filler, no preamble, no apologies, no sign-offs."` Previously only
  `"no filler"` was listed, leaving apologies uncovered.

**`supervisor/critique.py`**:
- `synthesize_results` system prompt: added `"No apologies."` alongside the existing
  `"No headers, no tables, no preamble labels. No questions at the end."` rule.
  Synthesis LLM now has explicit guidance not to apologise for missing data.

All 37 existing tests pass. No imports broken. All modified files ≤200 lines with
docstrings on every function.

---

## [Unreleased] — 2026-06-06 (patch 53)

### Fixed

#### P1: Empty Telegram message → 400 Bad Request
**`supervisor/interfaces/telegram_bot.py`**: `_handle_message` now strips and validates
`result.summary` before calling `reply_text`. Empty string → fallback text
`"DAG returned empty result. Unverified."` so Telegram never receives an empty body.

#### P2: GOAT hallucinates on missing file content
**`supervisor/dag_validator.py`**: New `_is_empty_file_read(result)` check — fires when
`source="file"`, `tool_called=True`, and `output` is empty. Reason code: `"empty_file_read"`.
Added at highest priority (before `unverified_execution`).
**`supervisor/supervisor.py`**: When any unsafe node has `reason="empty_file_read"`, summary
is set to `"File read confirmed but content not received. Unverified."` instead of generic
`"Unverified"`. Synthesis is skipped entirely — GOAT cannot hallucinate file content.

#### P3: Sensitive file content leaking to Telegram
**New file `supervisor/interfaces/content_filter.py`** (67 lines, single responsibility):
- `_ENV_LINE_RE` — matches `ALL_CAPS_KEY=value` at line start (standard env-file format).
- `_INLINE_KEY_RE` — matches `ALL_CAPS_KEY=value` anywhere in a line (inline references).
- `_CRED_RE` — matches values adjacent to `api_key|secret|password|token|credential|private_key`.
- `_looks_like_env_dump(text)` → True when ≥2 ALL_CAPS=value lines detected.
- `_contains_sensitive_path(text)` → True when `.env`, `api_keys`, `secrets`, etc. appear.
- `mask_sensitive(text)` — two-stage filter: env-dump detection first, credential keys always.
  When sensitive path or env dump detected, masks ALL inline `KEY=****` pairs.
  Always masks credential-named keys regardless of context.
**`supervisor/interfaces/telegram_bot.py`**: `mask_sensitive` applied to every outgoing message
before `reply_text` so API keys never reach the user in plain text.

#### P4: Missing source label — warning upgraded to blocking error
**`supervisor/task_prep.py`**: `prepare_tasks` now pre-sets `task.source = "planner"` for
every task that has an empty source. This audit placeholder satisfies DAG validation;
the runner overwrites it with the real source (`net|memory|file|generated`) during execution.
**`supervisor/workflow.py`**: `execute()` now separates validation issues by type. Issues
containing "source" raise `ValueError` (blocking DAG execution) rather than logging a warning.
Other structural issues continue to log as warnings without blocking.

---

## [Unreleased] — 2026-06-06 (patch 52)

### Fixed

#### DAG source enforcement — block generated source on execution tasks (Fixes 1–5)

Five interlinked fixes preventing DAG agents from returning `source='generated'` on tasks
that must invoke real tools. GOAT now detects and rejects hallucinated results.

**`supervisor/types.py`** — `AgentResult` gains three new fields (all with safe defaults):
- `tool_called: bool` — True when ≥1 tool was actually invoked during the task.
- `tool_name: str` — Primary tool name inferred from source (web_search / file_read / memory_search).
- `raw_output_hash: str` — SHA-256 16-char prefix of the task output for deduplication.
`SupervisorResult.to_dict()` includes all three fields in the per-task entries.

**`supervisor/workflow.py`** — `AgentResult` construction now populates all three new fields:
- `tool_called = task.source != "generated"` (inferred immediately after the runner returns).
- `tool_name` resolved via `_SOURCE_TOOL` dict (net→web_search, file→file_read, memory→memory_search).
- `raw_output_hash` computed via `hashlib.sha256(output.encode()).hexdigest()[:16]`.
`_SOURCE_TOOL: Final[dict[str, str]]` added as a module-level constant.

**`supervisor/dag_validator.py`** — Complete rewrite; source whitelist enforcement added:
- `_EXECUTION_ROLES: frozenset` — `{"researcher", "memory"}`: roles that must call a real tool.
- `_ROLE_ALLOWED_SOURCES: dict` — per-role source whitelist:
  - `researcher` → `{"net"}` only
  - `memory` → `{"memory"}` only
  - `coder`, `tool_caller` → `{"file", "net", "memory", "generated"}` (code gen allowed)
  - `critic`, `summarizer` → `{"generated", "file", "memory"}`
  - `planner` → `{"generated"}`
- `_is_unverified_execution(result)` — True when execution role has `tool_called=False`.
- `_is_source_violation(result)` — True when source not in role's whitelist.
- `validate_results` priority order: `unverified_execution > source_violation > net_error > stale_memory`.

**`supervisor/runners.py`** — Execution runners raise instead of silently accepting generated output:
- `_run_researcher`: raises `RuntimeError` if `r.source == "generated"` after `_call_with_tools`.
- `_run_tool_caller`: raises `RuntimeError` if `search=True` and `r.source == "generated"`.

**`supervisor/runner_memory.py`** — Tier 3 LLM fallback removed:
- When Tier 1 (memory_manager) and Tier 2 (Letta HTTP) both return empty, runner now returns
  `"ERROR: no memory results from any tier"` with `source="generated"` instead of calling the LLM.
- Unused imports (`_call_llm`, `_format_dep_context`) removed.
- `dag_validator` catches this node as `unverified_execution` and GOAT responds with `"Unverified"`.

**`supervisor/supervisor.py`** — GOAT rejects synthesis if any node failed source validation:
- `unsafe = [s for s in val_statuses if not s.safe]` collected after `validate_results`.
- Each unsafe node logged at WARNING with `task_id` and `reason`.
- `summary = "Unverified"` set before critique/auditor when any unsafe node exists.
- Legacy empty-source check retained as fallback.

All 37 existing tests pass. No imports broken.

---

## [Unreleased] — 2026-06-06 (patch 51)

### Fixed

#### Three tool definitions wired into registry and runner prompts

`FILE_GREP`, `FILE_INFO`, and `FILE_READ_LINES` were fully implemented in
`tools/file_grep.py`, `tools/file_info.py`, and `tools/file_read_lines.py`
but were missing from `tools/__init__.py` — not exported, not in `ALL_TOOLS`
or `FILE_TOOLS`, and not mentioned in any agent system prompt.

**`tools/__init__.py`**:
- Added imports for `FILE_GREP`, `FILE_INFO`, `FILE_READ_LINES`.
- Added all three to `__all__`, `ALL_TOOLS` (17 total), and `FILE_TOOLS` (9 total).

**`supervisor/runners.py`**:
- `_run_coder` and `_run_tool_caller` system prompts now list `file_grep(path, pattern)`,
  `file_info(path)`, and `file_read_lines(path, start_line, end_line)` alongside existing tools.

**`supervisor/identity.py`**:
- `direct_response` docstring updated to document the full FILE_TOOLS set (9 tools).

**Documentation** (`tools/README.md`, `readme.md`):
- Tool count corrected to 17; all three new tools added to the inventory tables.
- `FILE_TOOLS` convenience group entry updated in both files.

All three tools verified:
- `file_grep` — delegates to `EXECUTOR.grep()`; case-insensitive substring match; returns numbered matches.
- `file_info` — delegates to `EXECUTOR.info()`; returns name/path/type/size/timestamps/permissions.
- `file_read_lines` — delegates to `EXECUTOR.read_lines()`; 1-indexed; returns numbered line range.
Security checks (dotdot traversal, sensitive files, missing files) all pass via `EXECUTOR._resolve` + `_block`.

**Test fixture fix (`tests/tools/test_file_executor.py`)**:
6 write/list tests were failing because `_WS` lives in `file_executor_helpers.py`
but fixtures only reloaded `file_executor.py`. Extracted a `_reload(ws, mp, **env)` helper
that reloads `file_executor_helpers` first (picking up new `_WS`), then `file_executor`.
All 19 file executor tests now pass (was 13/19).

---

## [Unreleased] — 2026-06-06 (patch 50)

### Added

#### Source tagging, DAG validation, structured logging, auditor, require_source (Fixes 1-5)

**New modules (all <= 90 lines, single responsibility):**

- **`supervisor/source_types.py`** — `SourceTag = Literal["net","memory","file","generated"]`,
  `TOOL_SOURCE_MAP` (16 tool entries), `infer_source(called_tools) -> SourceTag` (priority: net > memory > file > generated),
  `TaggedResult(content, source, called_tools)` frozen dataclass.

- **`supervisor/structured_logger.py`** — `log_tool_call(tool_name, params, source, response)`
  emits one JSON record per tool call to `goat2.tool_calls.structured` logger.
  Fields: `tool_name`, `params`, `source`, `timestamp` (Unix epoch), `response_hash` (SHA-256 16-char prefix).
  Does not affect `goat2.supervisor` or `goat2.file_executor` loggers.

- **`supervisor/dag_validator.py`** — `validate_results(results) -> (results, statuses)`.
  Runs after all DAG nodes execute and before aggregation (critique/synthesize).
  Marks `source=net` results with error output as `unsafe` (reason: `net_error`);
  marks `source=memory` results containing `[stale]` as `unsafe` (reason: `stale_memory`).
  Returns unchanged results dict plus list of `ValidationStatus(task_id, safe, reason)`.

- **`supervisor/auditor.py`** — `run_auditor(results) -> AuditReport`.
  Runs after each execution. Groups results by role; compares all pairs using word-level
  Jaccard similarity. If similarity < 0.30 → logs WARNING and appends to `AuditReport.anomalies`.
  `AuditReport.clean` is True when no anomalies detected.

**Modified files:**

- **`supervisor/tool_runner.py`** — `_call_with_tools` now returns `TaggedResult` instead of `str`.
  Tracks `called_tools: list[str]` across all rounds; calls `log_tool_call` after each `_dispatch`
  for structured JSON logging. Bypass path (empty tools / `tool_calling=False`) returns
  `TaggedResult(content=..., source="generated")`.

- **`supervisor/runners.py`** — All five built-in runners unpack `TaggedResult` from
  `_call_with_tools` and set `task.source = r.source` before returning `r.content`.
  `_run_critic` and `_run_summarizer` (LLM-only) explicitly set `task.source = "generated"`.

- **`supervisor/runner_memory.py`** — Sets `task.source = "memory"` when Tier 1 (memory_manager)
  or Tier 2 (Letta HTTP) returns results; sets `task.source = "generated"` for Tier 3 fallback.

- **`supervisor/planner.py`** — `_run_planner` sets `task.source = "generated"`.

- **`supervisor/registry.py`** — `make_and_register` inner runner sets `task.source = "generated"`.

- **`supervisor/types.py`** — `SupervisorResult` gains two new fields with safe defaults:
  `sources: dict[str, str]` (task_id -> SourceTag) and `metadata_summary: str`.
  `to_dict()` includes both new fields.

- **`supervisor/identity.py`** — `direct_response` now returns `TaggedResult`.
  `conv_result` unpacks it and populates `SupervisorResult.sources = {"conv": tagged.source}`.

- **`supervisor/supervisor.py`** — Five fixes wired together:
  1. `[require_source: true]` prepended to `plan_ctx` for all ANALYTICAL/COMPLEX intents.
  2. `validate_results(results)` called after DAG execution, before critique/synthesize.
  3. Summary set to `"Unverified"` when any task result has an empty source tag.
  4. `run_auditor(results)` called after synthesis; anomalies captured in `metadata_summary`.
  5. `SupervisorResult.sources` populated from `{tid: r.source for tid, r in results.items()}`.

---

## [Unreleased] — 2026-06-05 (patch 48)

### Added

#### Temporal memory search — `memory_timeline`, `memory_recent`, `memory_debug_trace`; extended `memory_search`

**New memory modules (all ≤ 90 lines, single responsibility):**

- **`memory/time_parser.py`** — `parse_time_range(expr) → (start_epoch, end_epoch)`.
  Handles: `"today"`, `"yesterday"`, `"yesterday morning"` (06:00–12:00 local),
  `"last night"` (18:00–23:59 local), `"last Nh"`, `"last N days"`, ISO 8601 strings,
  and raw epoch floats. Default TZ: Europe/Bucharest; stored as UTC epoch.
- **`memory/temporal_filter.py`** — `filter_by_time(entries, start_ts, end_ts)`: post-filter
  by `created_at_ts`; entries with ts=0/absent are excluded when a filter is active (never
  guessed). `resolve_range(start_expr, end_expr)`: parses both expressions, honouring compound
  ranges like `"yesterday morning"` from the start param.
- **`memory/temporal_list.py`** — `gather_tier_list(layers, role, tier, limit)`: fan-out
  `list()` across all three tiers with (role, key) deduplication; used by `timeline` and `recent`.
- **`memory/temporal_search.py`** — `TemporalSearchMixin` with three public methods:
  - `timeline(role, start, end, tier, limit)` — entries in a time window, newest first
  - `recent(role, limit, tier)` — N most recent entries across tier(s)
  - `debug_trace(role, query, start, end)` — per-tier JSON with total/matched/snippet
- **`memory/memory_search.py`** — `MemorySearchMixin.search()` and `_fan_out_search()` gain
  `start_datetime`/`end_datetime` params; post-filtering with 4× oversample when active.
- **`memory/memory_manager.py`** — `MemoryManager` now inherits `TemporalSearchMixin`.

**New tool definitions (`tools/memory_temporal_tools.py`):**

- `MEMORY_TIMELINE` — `memory_timeline(start_datetime, end_datetime, tier="any", limit=100)`
- `MEMORY_RECENT` — `memory_recent(limit=50, tier="any")`
- `MEMORY_DEBUG_TRACE` — `memory_debug_trace(query, start_datetime=None, end_datetime=None)`

**Extended `MEMORY_SEARCH` (`tools/memory_tools.py`):**
- `memory_search` gains `start_datetime`, `end_datetime`, `tier` params (backward compatible;
  limit default raised 5 → 20). Responds honestly when no entries match the time window.

**Wired into:**
- `tools/__init__.py` — new tools imported; `MEMORY_TOOLS` group expanded to 6 tools.
- `tools/README.md`, `memory/README.md` — updated module maps and tool tables.

**Tests (`tests/memory/test_temporal_memory.py`):** 18 tests covering parser expressions
(`yesterday morning`, `last 24h`, ISO, unknown), `filter_by_time` (in-range, out-of-range,
no-ts), `resolve_range`, working-memory integration (store/search/list/recency),
no-timestamp no-crash, `debug_trace` structure, `timeline` empty range.

---

## [Unreleased] — 2026-06-05 (patch 47)

### Fixed

#### Conversational responses now receive CORE_TOOLS (memory + file access)

**Root cause:** `direct_response()` in `supervisor/identity.py` called `_call_llm`
directly, bypassing `_call_with_tools` entirely. Every CONVERSATIONAL intent went to the
LLM with zero tools — GOAT could not query memory or read files in normal conversation.

**Fix (`supervisor/identity.py`):**
- Replaced `from supervisor.llm_utils import _call_llm` with
  `from supervisor.tool_runner import _call_with_tools` (module-level).
- `direct_response()` now uses a deferred `from tools import MEMORY_TOOLS, FILE_TOOLS`
  (avoids `tools→agents→supervisor` circular import) and calls `_call_with_tools` with
  `MEMORY_TOOLS + FILE_TOOLS` as `CORE_TOOLS`. `tool_choice` defaults to `"auto"` so
  the model calls tools only when relevant.
- DAG paths (ANALYTICAL / COMPLEX) are unaffected — they route through their own runners.

---

## [Unreleased] — 2026-06-05 (patch 46)

### Added

#### Debug logging in `_run_tool_caller`; MEMORY_TOOLS schema verified

- **`supervisor/runners.py`** — added `import logging` and module-level
  `log = logging.getLogger("goat2.runners")`. `_run_tool_caller` now extracts
  the resolved tool list into `_tools` before the call and emits
  `log.debug("tool_caller: tools=%s", [t.name for t in _tools])`, making the
  exact schema list visible at `LOG_LEVEL=DEBUG`.
- **Schema validation** — all three MEMORY_TOOLS schemas verified against the
  OpenAI function-calling spec: `type=object`, `properties` with `type`+`description`
  on every field, `required` list present. `"default"` fields are informational
  (same pattern as `WEB_SEARCH.num_results`); `enum` constraints are valid JSON Schema.
  No schema errors found.

---

## [Unreleased] — 2026-06-05 (patch 45)

### Added

#### Memory tools: `memory_search`, `memory_get`, `memory_store` (`tools/memory_tools.py`)

Three new `ToolDefinition` instances that let the tool_caller agent actively query
and write all three memory tiers during conversation without leaving the tool loop.

- **`memory_search(query, limit=5)`** — calls `memory_manager.recall("goat", query)`
  via `MemoryRouter`; fan-out across working/Redis, episodic/ChromaDB, long-term/Letta.
  Returns `"[source] key: snippet"` lines, or a "No memory found" message.
- **`memory_get(key, tier="any")`** — calls `memory_manager.locate("goat", key)`;
  probes WORKING → EPISODIC → LONG_TERM in priority order when tier="any", or hits
  the specified tier directly. Returns `entry.content` or a "not found" message.
- **`memory_store(key, value, tier="working")`** — calls `memory_manager.store`;
  validates tier before writing; returns `"Stored 'key' in tier"` on success.
- All handlers use deferred `from memory.memory_manager import memory_manager` to
  avoid circular imports. All errors are caught and returned as `"ERROR: ..."` strings.

**Wired into:**
- **`tools/__init__.py`** — imported and added to `ALL_TOOLS`; new `MEMORY_TOOLS` group.
- **`supervisor/runners.py`** — `_run_tool_caller` now passes
  `([WEB_SEARCH] if search else FILE_TOOLS) + MEMORY_TOOLS` so memory tools are always
  available; system prompt updated to list `memory_search`, `memory_get`, `memory_store`.

---

## [Unreleased] — 2026-06-05 (patch 44)

### Fixed

#### web_search tool calling wired end-to-end in runners + tool_runner

**Root cause:** `researcher` was configured as `deepseek-r1` (`tool_calling=False`), so
`_call_with_tools([WEB_SEARCH], tool_choice="required")` silently fell back to `_call_llm`,
never calling the tool. `_run_tool_caller` used `tool_choice="auto"` and `FILE_TOOLS` for all
intents, so search tasks could select a file tool instead of `web_search`.

**Fix:**
- **`config/goat.toml`** — `researcher` changed from `deepseek-r1` to `deepseek-chat`
  (`tool_calling=True`); `tool_caller` confirmed as `deepseek-chat`.
- **`supervisor/runners.py`** — added `needs_internet(task) -> bool` (delegates to
  `_is_search_intent`); exported in `__all__`. `_run_tool_caller` now passes
  `tools=[WEB_SEARCH]` and `tool_choice="required"` when `needs_internet(task)` is True,
  falling back to `FILE_TOOLS` / `"auto"` otherwise. Trimmed docstrings to ≤90 lines.
- **`supervisor/tool_runner.py`** — removed 20-line docstring (replaced with 1-line);
  removed inline `ToolChoice` comment. File reduced from 101 to 80 lines.

---

## [Unreleased] — 2026-06-04 (patch 43)

### Fixed

#### ANALYTICAL web-search intents now include `tool_caller` in the DAG plan

**Root cause:** `supervisor.py` injects `[Lightweight: ≤2 tasks, no researcher]` for all
ANALYTICAL intents. The LLM planner followed this hint and omitted `tool_caller`, so
`web_search` was never called even though it was wired to that agent.

**Fix:**
- **`supervisor/classifier.py`** — added `_SEARCH_RE` pattern (DuckDuckGo / Romanian keywords:
  `search`, `look up`, `google`, `browse`, `internet`, `online`, `caută`, `net`) and
  `_is_search_intent(intent) -> bool`; exported in `__all__`. Compressed `_ACTION_VERBS`,
  `_PAST_PARTS`, and `_STATUS_RE` to recover the line budget (89 lines after).
- **`supervisor/supervisor.py`** — ANALYTICAL hint now reads
  `[Lightweight: ≤2 tasks, no researcher, must include tool_caller for web search]`
  when `_is_search_intent(intent)` is true, steering the planner to emit a `tool_caller` task.

---

## [Unreleased] — 2026-06-04 (patch 42)

### Added

#### `web_search` wired into FILE_TOOLS, researcher, and tool_caller agents

- **`tools/__init__.py`** — `WEB_SEARCH` added to `FILE_TOOLS`; all FILE_TOOLS consumers
  (coder, tool_caller) now receive web search automatically.
- **`supervisor/runners.py` — `_run_researcher`** — switched from `_call_llm` to
  `_call_with_tools([WEB_SEARCH])`; system prompt updated to instruct `web_search(query)` use.
- **`supervisor/runners.py` — `_run_tool_caller`** — system prompt updated to list
  `web_search(query)` alongside the file tools.
- **`tools/README.md`** — table and convenience-groups section updated for `FILE_SEARCH` and
  the expanded `FILE_TOOLS` list.

> `tools/web_search.py` was already implemented (DuckDuckGo instant-answers, `SEARCH_API_URL`
> override for SerpAPI/Tavily/Brave). This patch wires it into the agent layer.

---

## [Unreleased] — 2026-06-04 (patch 41)

### Added

#### `file_search` tool + exposed in coder/tool_caller agents

- **`tools/file_search.py`** — new `FILE_SEARCH` tool: `file_search(pattern, path)` uses
  `fnmatch` + `Path.rglob` to find files by name glob within the workspace. Returns relative
  paths, one per line; respects `EXECUTOR._resolve` security (path traversal, sensitive files).
  Max 100 results, configurable via `limit`.
- **`tools/__init__.py`** — `FILE_SEARCH` added to both `FILE_TOOLS` and `ALL_TOOLS`.
- **`supervisor/runners.py`** — `_run_coder` and `_run_tool_caller` system prompts updated to
  list `file_search(pattern, path)` alongside the existing file tools.

---

## [Unreleased] — 2026-06-04 (patch 40)

### Added

#### Debug logging in tool_runner (`supervisor/tool_runner.py`)

Added four `log.debug` statements to `_call_with_tools` to expose the tool-calling
pipeline at `LOG_LEVEL=DEBUG`:

| Location | Message | Diagnostic value |
|----------|---------|-----------------|
| Before bypass return | `tool_runner bypass: model=… tools=N tool_calling=…` | Reveals if tools list is empty or model has `tool_calling=False` |
| After schema build | `tool_runner: model=… sending tools=[…]` | Confirms tool names reach the API call |
| `msg.tool_calls` is empty | `tool_runner: round=N no tool_calls from …` | Shows model returned text instead of a tool call |
| `msg.tool_calls` non-empty | `tool_runner: round=N tool_calls=[…]` | Shows which tools the model invoked |

Enable with `LOG_LEVEL=DEBUG` or `logging.getLogger("goat2.tool_runner").setLevel(logging.DEBUG)`.

---

## [Unreleased] — 2026-06-04 (patch 38)

### Fixed

#### File-operation intents classified as ANALYTICAL (`supervisor/classifier.py`, `supervisor/supervisor.py`)

The LLM classifier often returned `CONVERSATIONAL` for simple file requests ("read file x",
"citește fișierul"), which routed them to `direct_response` — a plain LLM call with no tools.
The `file_op_result` workaround in `supervisor.py` also bypassed the DAG, so `tool_caller`
with its wired FILE_TOOLS schema was never reached.

**Fix in `classifier.py`**:
- After the LLM returns a depth, a post-classification guard upgrades
  `CONVERSATIONAL → ANALYTICAL` whenever `_is_file_op(intent)` is true:
  ```python
  if depth == IntentDepth.CONVERSATIONAL and _is_file_op(intent):
      return IntentDepth.ANALYTICAL
  ```
  COMPLEX is left untouched — a file op inside a multi-step research task stays COMPLEX.
- `_FILE_OP_RE` extended with Romanian `fișier` / `fisier` / `fișierul` pattern
  (`\bfi[sşș]ier\w*\b`), covering "citește fișierul" and "scrie în fișier".

**Cleanup in `supervisor.py`**:
- `file_op_result` / `_is_file_op` imports removed; the dead dispatch branch replaced with
  the direct `conv_result` call. `file_op_response.py` is now unused (retained on disk).

---

## [Unreleased] — 2026-06-04 (patch 36)

### Fixed

#### File tool schemas now passed to the API (`supervisor/runners.py`, `supervisor/task_prep.py`)

`_run_coder` and `_run_tool_caller` were calling `_call_with_tools(... task.tools ...)` where
`task.tools` started as `[]` and was populated externally by `task_prep.prepare_tasks`. The
external injection was fragile and the tools were mentioned only in the system prompt string
rather than being sent as actual OpenAI function-call schemas.

**Fix**:
- Both runners now import `FILE_TOOLS` inside their function body (deferred to avoid the
  `tools → agents → supervisor` circular import) and pass it directly:
  ```python
  from tools import FILE_TOOLS  # deferred: tools→agents→supervisor import cycle
  return await _call_with_tools(settings.agents.get("coder"), msgs, FILE_TOOLS, temperature=0.2)
  ```
- `task_prep.prepare_tasks` no longer injects tools: `_FILE_TOOL_ROLES` constant and the
  `task.tools = FILE_TOOLS` assignment removed. Docstring updated to reflect the reduced scope.

---

## [Unreleased] — 2026-06-04 (patch 34)

### Added / Rewritten

#### `FileToolExecutor` — central security gateway (`tools/file_executor.py`)

New module replacing per-file `_safe_path()` functions. Single responsibility for all
path resolution, validation, and I/O:

- **`~` / `$HOME` expansion** via `os.path.expandvars` + `os.path.expanduser` before resolution.
- **Symlink escape** blocked: `Path.resolve()` follows symlinks; resolved path must be inside `_WS`.
- **Dotdot traversal** blocked: relative paths resolved against workspace; out-of-workspace → `ERROR`.
- **Sensitive file blocklist**: `.env`, `id_rsa`, `id_ed25519`, `.pem`, `.key`, `.p12`, `.pfx`,
  `.git/`, `__pycache__/`, `secrets/`, `.ssh/`.
- **Size limits**: `FILE_READ_MAX_BYTES` (default 1 MB) and `FILE_WRITE_MAX_BYTES` (default 1 MB).
- **Atomic writes**: `tempfile.NamedTemporaryFile` + `os.replace` — no partial files on failure.
- **`GOAT_ALLOW_OUTSIDE_WORKSPACE=true`** + `GOAT_ALLOWED_PATHS` allowlist for explicit outside access.
- `tools/path_utils.py` is now a thin compat shim re-exporting `safe_path` from executor.

#### `file_list` tool (`tools/file_list.py`)

New tool added to `FILE_TOOLS` and `ALL_TOOLS`. Schema: `file_list({"path": "notes"})`.
Returns `"f name"` / `"d name"` lines up to `FILE_LIST_MAX_RESULTS` (default 200).
Passes through all executor security checks.

#### "tool not connected" instruction added to agent system prompts

`runners.py` (`_run_coder`, `_run_tool_caller`) and `file_op_response.py` now explicitly
instruct agents: "If a tool returns ERROR or is unavailable, say 'tool not connected' —
never hallucinate results." Prevents agents from inventing file contents or paths.

#### Tests (`tests/tools/test_file_executor.py`)

19 pytest tests covering all required scenarios:
valid read/write, reject `../`, reject absolute paths, reject symlink escape,
reject sensitive files (.env, .pem, `__pycache__`), reject oversized files,
atomic write safety (no `*.tmp` leftovers), and `file_list` with dir markers.

---

## [Unreleased] — 2026-06-04 (patch 32)

### Fixed

#### File tool path expansion (`tools/path_utils.py`, `tools/file_{create,read,write}.py`)

Paths containing `~` or `$HOME` were not expanded before the workspace boundary check,
causing them to be rejected even when they pointed inside the workspace.

**Fix**: extracted a shared `tools/path_utils.py` module with `safe_path(raw)`:
- `os.path.expanduser` + `os.path.expandvars` run before `Path.resolve()`.
- Relative paths still resolve against `WORKSPACE`; dotdot traversal is still blocked.
- New env var **`GOAT_ALLOW_OUTSIDE_WORKSPACE=true`** permits absolute paths outside
  the workspace (e.g. `~/Desktop/notes.txt`, `/tmp/scratch`). Default: `false`.
- Error message updated from "path traversal denied" to explain the env var.
- Tool descriptions and `tools/README.md` updated to document absolute path support.

#### File tools not available in conversational responses (`supervisor/classifier.py`, `supervisor/file_op_response.py`, `supervisor/supervisor.py`)

`CONVERSATIONAL` intent routed to `direct_response` (plain LLM, no tools), so requests
like "create a file at ~/Desktop/notes.txt" produced text instructions instead of acting.

**Fix**:
- `classifier._is_file_op(intent)` — regex-based check for file operation verbs
  (`create/write/read/delete/remove/save/edit` near `file`) or absolute paths (`~/`, `/home/`, `/tmp/`).
- New `supervisor/file_op_response.py` with `file_op_result(...)` — same signature as
  `conv_result`; calls `_call_with_tools` with `FILE_TOOLS` and the `tool_caller` model spec.
  Injects GOAT identity, behavior style, user profile, and memory context.
- `supervisor.py`: CONVERSATIONAL branch now dispatches `fn = file_op_result if _is_file_op(intent) else conv_result`.

---

## [Unreleased] — 2026-06-04 (patch 30)

### Fixed

#### Explicit file-tool awareness for coder and tool_caller (`supervisor/runners.py`)

Patch 28 added a generic `_TOOL_HINT` appended to the **end** of every agent's system
string. That was insufficient for two reasons:

1. **Position** — tool instructions buried after coding rules are deprioritised by the model.
2. **Accuracy** — researcher, critic, and summarizer use `_call_llm` with no tool schema;
   telling them to "use tools directly" was technically wrong.

**Fix**: the `_TOOL_HINT` constant and its `Final` import are removed. Instead:

- `_run_coder` and `_run_tool_caller` have their system prompts **rewritten tool-first**,
  naming each tool with its signature and the write-vs-create distinction up front:
  > "File tools available — call them directly: file_read(path),
  > file_write(path, content) to overwrite, file_create(path, content) to create.
  > Never ask the user to run shell commands."
  The tool schema is still injected via `_call_with_tools` / `task.tools`; the system
  prompt now ensures the model also knows to use them **proactively**.

- `_run_researcher`, `_run_critic`, `_run_summarizer` revert to plain strings — no tool
  mention, because those runners call `_call_llm` without a tool schema and genuinely
  cannot invoke file tools.

---

## [Unreleased] — 2026-06-04 (patch 29)

### Fixed

#### Synthesizer persona, tone, and format (`supervisor/critique.py`)

Three root causes of the wrong DAG synthesis behaviour, all fixed in `synthesize_results`:

**Model**: swapped `settings.agents.get("summarizer")` (llama-3.1-8b — unreliable at
multilingual persona following) for `settings.agents.get("planner")` (gpt-5.5), matching
the model used by the conversational path. `temperature=0.7` added for natural register.

**System instruction**: replaced "well-structured answer" wording (which invited headers
and tables) with a terse directive that references the user's tone and explicitly bans
preamble labels, headers, and tables. Summary-request brevity rule added inline:

> "For summary requests (rezumă, sumarizează, summarize): 3–5 sentences max."

**User message**: removed `"Synthesize the final answer."` sentence-ending trigger that
caused the model to echo it as a Romanian-language preamble label ("Răspuns Final:").
Critique block is now passed as plain `"Critique notes: {critique}"` without a markdown
`## Critique` header, eliminating the structural cue that induced formatted output.

---

## [Unreleased] — 2026-06-04 (patch 28)

### Fixed

#### Tool awareness in all agent system prompts (`supervisor/runners.py`)

Added `_TOOL_HINT: Final[str]` constant appended inline to the system prompt of every
built-in runner — researcher, coder, critic, summarizer, tool_caller:

> "Workspace tools available: file_read, file_write, file_create. Use them directly —
> never ask the user to run shell commands."

Coder and tool_caller can invoke these tools directly via the tool-calling loop;
researcher, critic, and summarizer cannot call them but the hint prevents them from
producing outputs that instruct the user to run shell commands manually. Zero extra lines
per runner (each change is a one-character `f`-string prefix on the last system string).

#### Cross-session summary applied to DAG synthesis (`supervisor/critique.py`, `supervisor/supervisor.py`)

`synthesize_results` now accepts `session_summary: str = ""` and passes it to
`_system_with_profile(profile, session_summary, style)`, making the synthesizer's system
prompt identical in structure to the conversational path:

```
GOAT identity
Learned user style — mirror it: …   (if style set)
User profile: …                     (if profile set)
Previous sessions: …                (if session_summary set)
Respond in {lang}.                  (if non-English)

You are a final synthesis agent. …
```

`GoatSupervisor.run()` passes `self._history.summary` as the new argument (inline
addition to the existing one-line call — zero net lines in `supervisor.py`).

---

## [Unreleased] — 2026-06-04 (patch 27)

### Fixed

#### Language detection in DAG agents (`supervisor/lang_detect.py`, `supervisor/task_prep.py`)

`detect_language(intent) -> str` uses `gpt-4o-mini` to identify the dominant language.
Returns `"English"` on empty input or any error (graceful fallback).

`prepare_tasks` is now `async` and returns the detected language. For non-English intents
it prepends `"Respond in {lang}.\n"` to the prompt of every `researcher`, `coder`,
`critic`, and `summarizer` task before DAG execution. English is left unmarked (models
default to English; no unnecessary noise in prompts). `FILE_TOOLS` injection is unchanged.

The detected language is threaded through `critique_results(…, lang)` (critic system
prompt gets `"Respond in {lang}."` prefix) and `synthesize_results(…, lang)` (same).

All call-site changes in `supervisor.py` are inline — zero net line delta.

#### Behavioral profile applied to synthesized DAG responses (`supervisor/critique.py`)

`synthesize_results` previously wrote a generic system prompt with no persona.
It now accepts `profile: str = ""` and `style: str = ""` and builds its system prompt
via `_system_with_profile(profile, style=style)` — the same helper used by the
conversational path — so the GOAT identity and learned behavioral style (mirror
instruction from Letta `goat/persona`) are applied to every DAG final answer.

`GoatSupervisor.run()` passes `self._user_profile` and `self._behavior_style` to
`synthesize_results` as inline argument additions (no new lines in `supervisor.py`).

---

## [Unreleased] — 2026-06-04 (patch 26)

### Added

#### File-tool wiring into GoatSupervisor (`supervisor/task_prep.py`, `supervisor/tool_runner.py`)

`FILE_TOOLS` (`file_read`, `file_write`, `file_create`) are now injected into every
`coder` and `tool_caller` task before DAG execution, enabling agents to read and write
workspace files during task execution.

**`supervisor/task_prep.py`** — single-responsibility task preparation:
- `prepare_tasks(tasks, memory_manager)` replaces the inline for-loop in `supervisor.py`.
- Injects `memory_manager` on all tasks; injects `FILE_TOOLS` on `coder`/`tool_caller` tasks.
- `from tools import FILE_TOOLS` is deferred inside the function body to break the import
  cycle `tools → agents.base_agent → supervisor → supervisor.supervisor`.

**`supervisor/tool_runner.py`** — agentic tool-calling loop:
- `_call_with_tools(spec, messages, tools, *, temperature)` — up to 8 LLM ↔ tool rounds.
- Respects `spec.tool_calling` (falls back to `_call_llm` for models that reject the tools param).
- Respects `spec.no_temperature` (omits temperature for o-series/gpt-5.5 in every round).
- Reuses `_get_client` from `supervisor.llm_utils`; no duplicate client cache.

**`supervisor/types.py`** — `AgentTask` gains `tools: list[ToolDefinition]` field (default `[]`).

**`supervisor/runners.py`** — `_run_coder` and `_run_tool_caller` now call `_call_with_tools`
instead of `_call_llm`; they pass `task.tools` directly so the tool set is planner-transparent.

---

## [Unreleased] — 2026-06-04 (patch 25)

### Fixed

#### Temperature omission for o-series / gpt-5.5 (`config/model_catalogue.py`, `supervisor/llm_utils.py`)

`ModelSpec` gains `no_temperature: bool = False`. When `True`, `_call_llm` omits the
`temperature` key from the API payload entirely — required by OpenAI o-series and gpt-5.5,
which reject the parameter rather than ignoring it.

- `gpt-5.5` in `MODELS` is marked `no_temperature=True`.
- Future o-series entries (`o1`, `o3`, `o4-mini`, …) must also set `no_temperature=True`.
- All other models are unaffected — `temperature` defaults unchanged at `0.2`.

---

## [Unreleased] — 2026-06-04 (patch 24)

### Added

#### Telegram interface — `supervisor/interfaces/telegram_bot.py`

`python-telegram-bot` v22.6 adapter wrapping `GoatSupervisor.run()` for Telegram.

- **Per-chat isolation**: `_sessions: dict[int, GoatSupervisor]` — one supervisor per
  `chat_id` so each user's conversation history, behavioral style, and memory are never mixed.
- **Token loading**: `load_toml().channel_str("telegram_token")` → `config/goat.toml [channels]`.
  Raises `RuntimeError` on startup if the token is blank.
- **Message filter**: `filters.TEXT & ~filters.COMMAND` — plain text only; `/commands` ignored.
- **Error handling**: exceptions logged at `ERROR` and returned to the user as `[error] …`.
- **Entry point**: `python -m supervisor.interfaces.telegram_bot` (long-polling).

New files: `supervisor/interfaces/__init__.py`, `supervisor/interfaces/telegram_bot.py`,
`supervisor/interfaces/README.md`.

---

## [Unreleased] — 2026-06-04 (patch 23)

### Added

#### File tools — `FILE_READ`, `FILE_WRITE`, `FILE_CREATE` (`tools/file_*.py`)

Three async `ToolDefinition` instances for reading, writing, and creating files
within the workspace. Designed for `CoderAgent` to persist generated code.

**Path safety (shared across all three tools):**

`_safe_path(rel) → Path | None` resolves the relative path against `_WORKSPACE`
using `Path.resolve()` (which follows symlinks) then asserts the result is still
under `_WORKSPACE` via `relative_to()`. Returns `None` for:
- Dotdot traversals: `../../etc/passwd`
- Absolute paths: `/etc/passwd` (Python's `/` operator discards the prefix)
- Symlinks pointing outside the workspace (resolved before the check)

`_WORKSPACE` defaults to the project root (`Path(__file__).resolve().parent.parent`)
and is overridable via the `GOAT_WORKSPACE` environment variable.

All handlers return `"ERROR: <reason>"` on failure — never raise — matching the
existing `CALCULATOR` and `WEB_SEARCH` convention.

---

**`tools/file_read.py` — `FILE_READ`**

`file_read(path: str, max_bytes: int = 32768) → str`

Reads up to `max_bytes` bytes (default 32 KiB) and decodes UTF-8 with replacement
characters for non-decodable sequences. Returns `ERROR:` if the path is unsafe,
the file is missing, or the target is a directory.

---

**`tools/file_write.py` — `FILE_WRITE`**

`file_write(path: str, content: str) → str`

Overwrites an **existing** file completely with UTF-8-encoded content. Deliberately
refuses to create new files — returns `ERROR: … use file_create …` — so agents
cannot silently create files at unintended paths. Returns `OK: wrote N bytes`.

---

**`tools/file_create.py` — `FILE_CREATE`**

`file_create(path: str, content: str = "", exist_ok: bool = False) → str`

Creates a new file and any missing parent directories. Fails with `ERROR:` if the
file already exists and `exist_ok=false` (default). When `exist_ok=true` the file
is overwritten. Returns `OK: created '<path>' (N bytes)` or `OK: overwrote ...`.

---

**`tools/__init__.py`** — updated:
- `FILE_READ`, `FILE_WRITE`, `FILE_CREATE` added to `__all__` and `ALL_TOOLS`
- New `FILE_TOOLS: list[ToolDefinition] = [FILE_READ, FILE_WRITE, FILE_CREATE]`
  convenience group for injecting just file capabilities into an agent

**New files:** `tools/file_read.py`, `tools/file_write.py`, `tools/file_create.py`
**Modified files:** `tools/__init__.py`, `tools/README.md`

---

## [Unreleased] — 2026-06-04 (patch 22)

### Changed

#### `.env` replaced by `config/goat.toml` as primary local-dev config

`python-dotenv` + `.env` removed as the configuration mechanism. `config/goat.toml`
is now the single file for local development. Env vars still override every toml
value — set them in CI/production instead of editing the file.

**Resolution order (applied uniformly to every setting):**
```
environment variable  →  goat.toml  →  hard-coded default
```

---

##### New file: `config/goat.toml`

Five sections mirror the Settings hierarchy:

| Section | Contents |
|---------|----------|
| `[model]` | `default`, `provider`, `supervisor` model keys |
| `[agents]` | Per-role model keys (`planner`, `researcher`, `coder`, `critic`, `summarizer`, `tool_caller`, `memory`) |
| `[api_keys]` | `openai`, `deepseek`, `groq` — local dev only; blank = use env var |
| `[memory]` | `letta_base_url`, `letta_api_key`, `letta_llm_model`, `letta_embed_model`, `letta_token_limit`, `chroma_persist_dir` |
| `[channels]` | `telegram_token`, `telegram_enabled` — wired when Telegram interface is built |

---

##### New file: `config/toml_loader.py`

`_load_raw()` — reads `config/goat.toml` using `tomllib` (stdlib, Python ≥3.11) with
`tomli` as a fallback for older interpreters; returns `{}` silently on any error so the
system degrades to env vars + hard-coded defaults when the file is absent.

`TomlConfig` — typed read-only view with one accessor per value type:
`model()`, `agent()`, `api_key()`, `memory_str()`, `memory_int()`, `channel_str()`,
`channel_bool()`. All return a safe default rather than raising on missing keys.

`load_toml() → TomlConfig` — module-level singleton factory called once at import
time in each sub-module that needs it.

---

##### Updated `config/agent_models.py` — 5-step model key resolution

`_key(env_var, toml_role, role_default)` now resolves through five layers:
```
AGENT_<ROLE>_MODEL  →  DEFAULT_MODEL env  →  [agents].<role>  →  [model].default  →  role default
```
The new `toml_role` parameter (the `[agents]` key name) is always the same as the
field name — `_key("AGENT_PLANNER_MODEL", "planner", "gpt-4o")`.

##### Updated `config/api_keys.py` — toml layer added

`_api_key(env_var, toml_provider)` replaces the bare `_optional()` call:
```
OPENAI_API_KEY env  →  goat.toml [api_keys].openai  →  ""
```
The `for_provider` error message now mentions `goat.toml` as an alternative to env vars.

##### Updated `config/settings.py` — toml-aware helpers; dotenv removed

- `python-dotenv` loading (`try: from dotenv import load_dotenv`) removed.
- `_toml = load_toml()` module-level singleton.
- `_e(env_var, toml_val, default) → str` — three-layer resolver for string settings.
- `LettaConfig` — all five fields resolved via `_e()` + `_toml.memory_str/int()`.
- `SupervisorConfig.model_key` — resolved via `_e("SUPERVISOR_MODEL", _toml.model("supervisor"), "gpt-4o")`.
- `Settings.default_model` / `default_provider` — read toml `[model].default` / `[model].provider` as fallback.

**No changes to any caller** — `config.settings` still exports all names.

**New files:** `config/goat.toml`, `config/toml_loader.py`
**Modified files:** `config/agent_models.py`, `config/api_keys.py`, `config/settings.py`, `config/README.md`

---

## [Unreleased] — 2026-06-04 (patch 21)

### Changed

#### `config/settings.py` split into focused sub-modules; `DEFAULT_MODEL` / `DEFAULT_PROVIDER` added

`settings.py` was 228 lines and mixed unrelated concerns. Split into four files
(all ≤ 90 lines); all existing `from config.settings import …` call sites unchanged
— `settings.py` re-exports every name from sub-modules.

**New files:**

- **`config/model_catalogue.py`** — `Provider` enum, `ModelSpec` dataclass (now with
  `tool_calling: bool = True` capability flag), `MODELS` dict, `get_model()`.
  `deepseek-r1` is registered with `tool_calling=False`.

- **`config/api_keys.py`** — `APIKeys` dataclass, `PROVIDER_BASE_URLS` dict.

- **`config/agent_models.py`** — `AgentModels` dataclass with `_key()` resolution helper.
  Model key resolution order: `AGENT_<ROLE>_MODEL` → `DEFAULT_MODEL` → role default.

**`config/settings.py`** (now 90 lines) retains:
- dotenv loading + `_optional` helper
- `LettaConfig`, `SupervisorConfig` (supervisor also respects `DEFAULT_MODEL`)
- `Settings` with two new fields: `default_model: str` and `default_provider: str`
- `Settings.validate()` — now also checks `DEFAULT_PROVIDER` against known providers
- `settings` singleton
- Re-exports all names for backward compatibility

**`DEFAULT_MODEL` behaviour:** setting `DEFAULT_MODEL=gpt-4o-mini` switches every
agent (and the supervisor) to that model without touching role-specific env vars.
Role-specific vars (`AGENT_PLANNER_MODEL`, …) still take precedence.

**`DEFAULT_PROVIDER` behaviour:** stored on `Settings.default_provider`; validated
in `Settings.validate()` — an unknown provider name raises `EnvironmentError`.
Does not automatically filter models; intended for operator-level documentation
and future routing logic.

---

#### `researcher.py` — tool suppression via `ModelSpec.tool_calling`, not a hardcoded frozenset

**Root cause:** `_NO_TOOL_MODELS: frozenset[str] = frozenset({"deepseek-reasoner"})` in
`researcher.py` was a hardcoded capability check that would silently break if the
model ID changed or a new no-tool model was added.

**Fix:** `ModelSpec` gains `tool_calling: bool = True`. `deepseek-r1` is registered
with `tool_calling=False` in the MODELS catalogue — the single source of truth.
`ResearcherAgent.execute()` now checks `self.spec.tool_calling` directly:

```python
tool_override: list | None = [] if not self.spec.tool_calling else None
```

Any model added to MODELS with `tool_calling=False` is automatically handled by
all agents that check the flag — no per-agent list to maintain.

`researcher.py` was also 118 lines (over the 90-line limit). The 39-line
`_SYSTEM_PROMPT` constant is extracted to `agents/researcher_prompt.py`;
`researcher.py` drops to 39 lines.

**New file:** `agents/researcher_prompt.py`
**Modified files:** `config/model_catalogue.py` (new), `config/api_keys.py` (new),
`config/agent_models.py` (new), `config/settings.py`, `agents/researcher.py`,
`config/README.md`

---

## [Unreleased] — 2026-06-04 (patch 20)

### Fixed

#### Intent classifier mis-routing first-person status updates (`supervisor/classifier.py`)

Statements like "I'm working on X" or "I just deployed Y" were being classified as
`ANALYTICAL` because the LLM saw technical vocabulary and inferred a structured task.
These are status reports — no DAG, no planner, no researcher needed.

**Root cause:** `_CLASSIFIER_SYSTEM` had no example of status updates as conversational,
and the LLM consistently weighted technical nouns over the first-person present-continuous
framing.

**Fix — pre-LLM regex gate in `classify_intent`:**

`_is_status_update(intent) → bool` (pure function, PyO3 candidate) runs before any LLM
call. If it matches, `classify_intent` returns `CONVERSATIONAL` immediately — technical
vocabulary in the payload is never evaluated.

The regex covers three structural surfaces:
- **Contractions** — `I'm [action-verb]`, `I've [past-participle]` (no space between `I` and `'m`/`'ve`, handled as literal prefixes `i'm`, `i've` to avoid `\s+` missing the match)
- **Spaced forms** — `I am [action-verb]`, `I have [past-participle]`, `I just [any-verb]`
- **Simple past** — `I finished/built/deployed/merged/…`

Cognitive and volitional verbs (`wondering`, `thinking`, `trying`, `hoping`) are
intentionally excluded from the action-verb list, so "I'm wondering how X works" still
reaches the LLM for proper classification.

Trailing `?` is checked after the regex — "I'm working on X, how should I approach Y?"
bypasses the gate and goes to the LLM.

`_CLASSIFIER_SYSTEM` prompt also updated to include `"status updates ('I'm working on X',
'I just did Y')"` as a conversational example, improving LLM accuracy for edge cases the
regex doesn't cover (e.g. multi-sentence status updates).

**Modified files:** `supervisor/classifier.py`

---

## [Unreleased] — 2026-06-04 (patch 19)

### Added

#### Memory quality system — confidence scoring, pollution guard, and precision metrics

Three coordinated improvements that prevent low-quality data from reaching Letta core
memory and add observability to the router's latency distribution.

---

##### 1. Fact confidence scoring (`supervisor/info_extract.py`, `supervisor/info_types.py`)

`maybe_store_info` now classifies every extracted fact as **explicit** (the user stated
it directly) or **inferred** (deduced from context) and routes each kind to the correct
memory tier.

**New module — `supervisor/info_types.py`:**
- `FactKind = Literal["explicit", "inferred"]` — Rust-ready type alias
- `ScoredFact` TypedDict — `{key, value, kind}`, no `dict[str, Any]`
- `INFERRED_TTL: Final[int] = 604_800` — 7 days in seconds, single source of truth

**Updated `_SYSTEM` prompt** — asks the LLM to return
`{"facts":[{"key":"k","value":"v","kind":"explicit"}]}` instead of the previous
`{"pairs":{…}}` shape. Explicit/inferred classification is model-side.

**Routing logic in `maybe_store_info`:**
```
explicit facts → PollutionGuard.validate() → mm.set_block() → Letta core
inferred facts → mm.store(EPISODIC, metadata={tags:["inferred"], expires_at_ts: now+7d})
```
Inferred facts land in ChromaDB with `expires_at_ts` in metadata; callers that want to
honour the TTL filter by comparing `expires_at_ts` against `time.time()`.

**`memory/types.py`** — `MemoryEntryMetadata` gains
`expires_at_ts: NotRequired[float]` (epoch-seconds) so the TTL is typed, not a raw
metadata string.

---

##### 2. Pollution guard (`memory/pollution_guard.py`)

New module with a pure validation function and a thin logging wrapper.

**`validate_fact(key, value, kind, existing_block) → GuardResult`** — pure, PyO3 candidate:
- Returns `"blocked"` if `kind != "explicit"` (inferred facts never touch Letta core)
- Returns `"blocked"` if the key matches `_BLOCKED` or ends in `_id`
- Returns `"conflict"` if the key exists in `existing_block` with a different value
  (flags without auto-overwriting — caller decides whether to skip or escalate)
- Returns `"allowed"` otherwise

**`PollutionGuard`** — stateless class that wraps `validate_fact` with logging:
- `"conflict"` → `log.warning` with existing vs new value
- `"blocked"` → `log.debug`

**`GuardDecision = Literal["allowed", "blocked", "conflict"]`** and `GuardResult` TypedDict
are exported for callers that need to inspect the outcome (e.g. future audit trail).

---

##### 3. Precision and latency percentiles (`memory/router/layer_stats.py`)

`LayerStats` and `LayerStatsTracker` extended to track P50/P95/P99 latency per layer.

**`_SAMPLE_CAP: Final[int] = 1000`** — ring-buffer size per layer (oldest samples evicted
automatically via `deque(maxlen=_SAMPLE_CAP)`).

**`_percentile(samples: list[float], p: float) → float`** — pure linear-interpolation
percentile over a sorted sample list; PyO3 candidate. Returns `0.0` on empty input.

**`LayerStats`** gains three new fields (all default to `0.0`):
```python
p50_ms: float = 0.0
p95_ms: float = 0.0
p99_ms: float = 0.0
```
Fields are computed at snapshot time from the sorted sample buffer — `LayerStats` itself
remains a dumb dataclass (no internal mutable state).

**`LayerStatsTracker`:**
- `__init__` adds `self._samples: dict[LayerName, deque[float]]`
- `record()` appends `timing.duration_ms` to the layer's sample buffer after updating
  the existing counters (backward-compatible)
- `get()` now calls `sorted(self._samples[layer])` and passes the result to `_percentile`
  for all three percentile fields before constructing the returned `LayerStats`

**`snapshot()`** is unchanged in signature — it delegates to `get()` per layer so all
callers automatically see percentile-enriched stats without modification.

---

**New files:** `supervisor/info_types.py`, `memory/pollution_guard.py`
**Modified files:** `supervisor/info_extract.py`, `memory/router/layer_stats.py`, `memory/types.py`, `memory/README.md`, `memory/router/README.md`

---

## [Unreleased] — 2026-06-04 (patch 18)

### Fixed

#### `persona` memory block missing from Letta agent creation payload (`memory/letta_registry.py`)

`do_set_block` (called by `save_style`) patches an existing block via `PATCH /v1/agents/{id}/core-memory/blocks/persona`. When the Letta agent was created without a `"persona"` block in its `memory_blocks` payload, Letta returns 404 on the PATCH → `probe.mark_unavailable()` is called → `set_block` returns `False` → behavioral style is never written.

**Fix:** `_create()` now always includes both `"persona"` and `"human"` blocks in the creation payload:

```python
"memory_blocks": [
    {"label": "persona", "value": "", "limit": ...},   # behavioral style profile
    {"label": "human",   "value": "", "limit": ...},   # user info (info_extract)
]
```

Both blocks start empty. `"persona"` is populated by `save_style` after the first session's behavioral analysis. `"human"` is populated by `maybe_store_info` as the user reveals facts.

The previous `"persona"` initial value (`"I am the memory store for the GOAT 2.0 {role} agent…"`) was also removed: it was non-empty non-profile text that `load_style → deserialize` silently discarded, serving no purpose and potentially confusing operators inspecting block contents in the Letta UI.

**Note:** existing Letta agents created before this fix will still be missing the `"persona"` block. Delete them from the Letta UI (or via `DELETE /v1/agents/{id}`) so they are recreated with the correct payload on the next GOAT session start.

---

## [Unreleased] — 2026-06-04 (patch 17)

### Changed

#### Conversational responses — gpt-4o at temperature 0.7 (`supervisor/identity.py`)

`direct_response` previously used `settings.agents.get("memory")` (gpt-4o-mini) with the default temperature 0.2. Switched to `settings.agents.get("planner")` (gpt-4o) with an explicit `temperature=0.7`. Higher temperature produces more natural, varied conversational replies; gpt-4o produces significantly better quality for the conversational path.

#### DAG agents — temperature 0.2 (`supervisor/llm_utils.py`)

Changed `_call_llm` default `temperature` from `0.4` → `0.2`. All DAG-path callers that do not pass an explicit temperature (researcher, critic, summarizer, tool_caller, planner, memory-distillation) automatically inherit this change. The coder runner already had an explicit `temperature=0.2` and is unchanged. The extraction runners (`info_extract`, `behavior_analyzer`) already use explicit `temperature=0.0` and are unchanged. The new `direct_response` override (`temperature=0.7`) takes precedence over the default.

#### `finalize_behavior` — explicit error logging when persona block is not written (`supervisor/behavior_session.py`, `supervisor/behavior_store.py`)

`save_style` previously returned `None` so `finalize_behavior` could not tell whether the Letta write succeeded or silently failed. Two-layer fix:

- **`behavior_store.save_style`** now returns `bool` (True on success). The two `log.warning` calls for failure cases (`set_block returned False`, exception) are promoted to `log.error` so they appear regardless of the `LOG_LEVEL` setting.
- **`behavior_session.finalize_behavior`** captures the return value (`saved = await save_style(...)`). On `saved=False` it logs at `log.error` with the Letta URL and a `curl` health-check command, making the root cause immediately actionable.

---

## [Unreleased] — 2026-06-04 (patch 16)

### Changed

#### `MemoryManager.recall()` now routes through `MemoryRouter` (`memory/memory_manager.py`)

All untagged fan-out memory queries are now intelligently routed instead of blindly querying all three tiers in parallel.

**What changed:**

- `MemoryManager` gains `self._router: MemoryRouter | None = None` (lazy, `None` until first `recall()` call).
- `_get_router()` — lazily constructs `MemoryRouter(self)` on first use. The import is deferred inside the method (`from memory.router import MemoryRouter`) to avoid any circular-import risk at module load time.
- `recall()` is overridden in `MemoryManager` (takes precedence over `MemorySearchMixin.recall()` via Python MRO). The override:
  - When `tags=None` (the common case): delegates to `MemoryRouter.search()` — classify intent, route to 1–3 layers based on confidence, record timing, adapt preferences.
  - When `tags is not None`: falls back to `_fan_out_search()` unchanged, because `MemoryRouter.search()` does not support tag filtering.

**What is unchanged:**
- `search(memory_type=<specific>)` — still goes directly to the named tier.
- `search(memory_type=None)` / `_fan_out_search()` — still available as explicit full fan-out, now used only by the tags fallback path.
- Public signature of `recall()` is identical — no call-site changes required anywhere.
- `memory_search.py` is untouched.

---

## [Unreleased] — 2026-06-04 (patch 15)

### Added

#### Intelligent memory router (`memory/router/`) — 10 modules, all ≤ 90 lines

Drop-in replacement for `MemoryManager.search` / `recall` that classifies query intent, routes to the optimal layer(s), adapts routing preferences from observed performance, and caches routing decisions for repeated patterns.

**Architecture — pure pipeline:**
```
classify_query(query) → (QueryType, strength)
     ↓
make_route_key(query, type) → RouteKey  →  RouteCache.get()  →  cached RoutingDecision
     ↓ (cache miss)
preferred_layers(type, LayerStatsTracker.snapshot()) → tuple[LayerName, ...]
compute_confidence(type, strength, preferred_layer.hit_rate) → Confidence
make_decision(type, confidence, preferred) → RoutingDecision  →  RouteCache.put()
     ↓
execute_route(decision, role, query, layers, record=tracker.record) → list[MemoryEntry]
     ↓ (records LayerTiming after each call)
LayerStatsTracker.record(LayerTiming)  →  adaptive preference on next query
```

**Routing strategy:**

| Confidence | Layers tried | Execution |
|------------|-------------|-----------|
| ≥ 0.70 | 1 (single best) | one async call |
| 0.40 – 0.69 | 2 (sequential) | first; fall through to second if empty |
| < 0.40 | 3 (fan-out) | `asyncio.gather`, deduplicated, `created_at_ts` descending |

**Query type → layer affinity (static, 70 % weight):**
- `temporal` ("last week", "yesterday") → `episodic` first
- `recency` ("latest", "just now") → `working` first
- `semantic` (4+ word conceptual) → `episodic` first
- `generic` / `unknown` → equal weight, confidence ≤ 0.40 → fan-out

**Adaptive component (30 % weight):** `LayerStatsTracker` accumulates call count, total latency, and hit count per layer. `preferred_layers` blends `0.70 × affinity + 0.30 × hit_rate` so layers that consistently return results rise in the ranking over time.

**Cache:** `RouteCache` is a 128-slot LRU keyed by `make_route_key` (MD5 of first 5 non-stopword tokens + query type). Identical phrasings of the same intent skip re-classification. Cached decisions are returned with `RoutingDecision.cached=True`.

**Rust-readiness:**
- `Confidence`, `RouteKey`, `Millis` — NewType wrappers (zero runtime cost)
- `RoutingDecision`, `LayerTiming` — `frozen=True` dataclasses (immutable, hashable)
- `CONF_HIGH`/`CONF_LOW` — single `Final[float]` source in `types.py`, shared by `decision.py` and `executor.py`
- All `dict` annotations are fully typed (`dict[LayerName, LayerStats]`, not `dict[str, Any]`)
- `classify_query`, `compute_confidence`, `preferred_layers`, `make_route_key` — pure functions annotated as PyO3 candidates

**New files:** `memory/router/__init__.py`, `types.py`, `classifier.py`, `confidence.py`, `preferences.py`, `decision.py`, `cache.py`, `layer_stats.py`, `executor.py`, `router.py`, `README.md`

---

## [Unreleased] — 2026-06-04 (patch 14)

### Fixed

#### Technical metadata leaking into system prompt (`supervisor/identity.py`, `supervisor/info_extract.py`)

Keys such as `agent_id`, `passage_id`, `search_key`, `limit` were ending up in the Letta `human` block and being injected into GOAT's system prompt as if they were user facts.

**Root cause — two points of failure:**

1. **Write-time (`info_extract.py`)**: `_SYSTEM` said "names, codes, IDs" without qualification. The LLM correctly extracted `agent_id`, `passage_id` etc. from conversations about the memory system and stored them as user facts. `_merge` then persisted them unconditionally.

2. **Read-time (`identity.py`)**: `_system_with_profile` injected whatever was in the `human` block verbatim with no filtering.

**Fix — two-layer defence:**

- **`info_extract._SYSTEM`** — prompt now explicitly names the forbidden classes: "Never extract system or technical fields such as agent_id, passage_id, search_key, limit, offset, score, memory_type, or any internal identifier."
- **`info_extract._BLOCKED`** + **`_merge`** — write-time blocklist (`frozenset` of 13 keys) applied in `_merge` for both the existing block and incoming `new_pairs`. Also rejects any key ending in `_id` via pattern check. This cleans up already-stored garbage on the next write.
- **`identity._BLOCKED_KEYS`** + **`_filter_profile`** — read-time filter applied in `_system_with_profile` before building the `User profile:` section. Strips blocklisted keys and keys ending in `_id`. If all lines are filtered out the `User profile:` section is omitted entirely.

#### System prompt generating questions at end of response (`supervisor/identity.py`)

`GOAT_SYSTEM` instructed "No filler, no preamble, no sign-offs" but said nothing about questions. The model kept appending clarifying questions because nothing prohibited them.

**Fix:** Added "Never end a response with a question." to `GOAT_SYSTEM` as a fourth sentence on the same line (no line-count increase).

---

## [Unreleased] — 2026-06-04 (patch 13)

### Fixed

#### `finalize_behavior` not writing to Letta `persona` block (`supervisor/behavior_store.py`, `supervisor/behavior_analyzer.py`, `supervisor/behavior_session.py`)

Three silent failure paths prevented the behavior profile from ever reaching Letta:

1. **`save_style` ignored `set_block`'s return value** — when Letta is unreachable, `MemoryManager.set_block` returns `False` without raising an exception. The bare `try/except` block caught nothing; the `False` return was discarded silently. Fixed by capturing `ok = await mm.set_block(...)` and logging `warning` when `ok` is `False`.

2. **`analyze_style` swallowed all exceptions silently** — `except Exception: return existing` gave no indication of LLM call failures, JSON parse errors, or network issues. Fixed by logging `warning` with the exception message. Also logs `warning` when the LLM returns an empty `profile` dict.

3. **`behavior_session.py` had no logging** — impossible to distinguish "style unchanged, skip write" (correct) from "analyze failed, returned existing" (bug). Added `log.info` at every decision point: turn count, existing style state, analyze result, write/skip decision.

**Logging added** (logger `goat2.supervisor.behavior`):
- `behavior_store.load_style` — `debug` on ignored initial block; `debug` on field count loaded; `warning` on Letta read error
- `behavior_store.save_style` — `debug` on skip; `info` on successful write with char count; `warning` on `False` return or exception
- `behavior_analyzer.analyze_style` — `debug` on too-few-turns skip; `warning` on empty LLM profile; `debug` on field count produced; `warning` on exception
- `behavior_session.finalize_behavior` — `debug` on mm/history skip; `info` on turn count + existing style; `info` on empty/unchanged/updated profile; delegates write logging to `save_style`

---

## [Unreleased] — 2026-06-04 (patch 12)

### Added

#### Behavioral learning — GOAT mirrors user communication style (`supervisor/behavior_*.py`, `supervisor/session_init.py`)

GOAT now observes the user's communication style (formality, tone, vocabulary, language, humor, message length, distinctive patterns) across turns and sessions, builds a behavioral profile, and mirrors it in every response.

**New modules (all ≤ 90 lines, single responsibility):**

- **`behavior_profile.py`** — `BehaviorProfile` TypedDict with 7 optional style fields. `serialize` / `deserialize` are pure helpers that convert the profile to/from a compact `key: value` text block suitable for Letta storage.
- **`behavior_analyzer.py`** — `analyze_style(user_turns, existing)` sends recent user messages to `gpt-4o-mini` (JSON mode, `temperature=0`) with a style-extraction system prompt. Merges the new observations into the existing profile via `{**deserialize(existing), **new}`. Requires ≥ 2 user turns; returns `existing` unchanged on failure.
- **`behavior_store.py`** — `load_style(mm)` / `save_style(mm, style)` read and write the Letta `goat/persona` core-memory block. `load_style` validates the block content with `deserialize` — if no recognized style fields are found (e.g. the initial agent description), it returns `""` so the first session starts with a clean slate.
- **`behavior_mirror.py`** — `mirror_instruction(style)` collapses the multi-line profile into a single-line directive (`"Learned user style — mirror it: formality: casual; tone: technical; …."`) injected as the first appendix to `GOAT_SYSTEM`. Returns `""` when the style is empty.
- **`behavior_session.py`** — `finalize_behavior(mm, history, current_style)` orchestrates session-end analysis: extracts user turns from `ConversationHistory`, calls `analyze_style`, and calls `save_style` only when the profile changed.
- **`session_init.py`** — `init_session(mm)` replaces the inline 3-line first-run init block in `GoatSupervisor.run()`. Runs `load_user_profile`, `load_session_summary`, and `load_style` concurrently via `asyncio.gather`, returning `(profile, ConversationHistory(summary), style)`.

**Modified files:**

- **`supervisor/identity.py`** — `_system_with_profile(profile, summary, style)` gains a `style` parameter. When set, `mirror_instruction(style)` is prepended to the system prompt before the user profile and session summary. `direct_response` and `conv_result` forward `style` through.
- **`supervisor/supervisor.py`** — `__init__` adds `self._behavior_style: str = ""`. First `run()` call uses `init_session` (concurrent, replaces sequential inline block). `conv_result` receives `self._behavior_style`. New `finalize_session()` method delegates to `finalize_behavior`.
- **`cli.py`** — The `while True` loop is wrapped in `try/finally` so `sv.finalize_session()` is always called on exit (normal quit, EOF, `KeyboardInterrupt`).

**Data flow:**
```
Session start: load_style → Letta goat/persona → _behavior_style cached on supervisor
Every CONVERSATIONAL response: _system_with_profile(..., style=_behavior_style)
  → "Learned user style — mirror it: …." prepended to GOAT_SYSTEM
Session end: finalize_behavior → analyze_style → PATCH Letta goat/persona
  → profile available immediately on the next session startup
```

---

## [Unreleased] — 2026-06-04 (patch 11)

### Fixed

#### Letta archival-memory search always returning empty (`memory/letta_ops_retrieve.py`, `memory/letta_helpers.py`)

**Root cause — two bugs, one compounding the other:**

1. `do_search` called `GET /v1/agents/{id}/archival-memory/search` (the semantic/vector
   endpoint). This endpoint requires the Letta agent to have an embedding model configured.
   GOAT creates agents without an `embedding` field, so `embedding_config` is `null` →
   Letta's vector index is empty → the endpoint always returns `{"results": [], "count": 0}`.

2. Setting `embedding` on a Letta agent (via `PATCH /v1/agents/{id}`) causes Letta to try
   to call the OpenAI embedding API on every `POST /archival-memory` → Letta's own OpenAI
   key is not configured → every store request returns `500 Internal Server Error`. So
   enabling embeddings breaks writes. Not configuring them breaks semantic search.

**Fix — switch to keyword search:**
- `do_search` now calls `GET /v1/agents/{id}/archival-memory?search={kw}&limit={n}` (the
  text-keyword endpoint, same path `do_retrieve` already used). This endpoint works without
  embeddings and returns results reliably.
- The keyword `kw` is the longest word > 3 chars from the query, stripped of punctuation.
  This covers the common case where the user's current intent shares a content word with
  stored turns (e.g. "care este codul" → `kw="codul"` finds all code-related turns).

**Refactor — `_passage_to_entry` helper (`memory/letta_helpers.py`):**
- Extracted shared `MemoryEntry` construction from `do_retrieve` and `do_search` into
  `_passage_to_entry(p, role, fallback)` in `letta_helpers.py`. Handles both `text` and
  `content` field names and both `timestamp` and `created_at` timestamps across endpoints.
  Reduces `letta_ops_retrieve.py` from 83 → 60 lines.

---

## [Unreleased] — 2026-06-04 (patch 10)

### Changed

#### GOAT identity rewritten — minimal, tone-mirroring (`supervisor/identity.py`)
- `GOAT_SYSTEM` reduced from 7 sentences to 3:
  ```
  "You are GOAT — a personal assistant with persistent memory.
   Mirror the user's language, tone, and register in every reply.
   No filler, no preamble, no sign-offs."
  ```
- Removed: fixed-language prohibitions ("Certainly!", "Great question!"), the
  one-clarifying-question rule, the memory disclaimer prohibition, and the
  "You have persistent memory" capability statement. Memory context now flows
  in via the `[Memory]` system message and prior-session summary — the model
  sees it directly and uses it without being told to.
- "Mirror the user's language, tone, and register" replaces all tone/style
  instructions. The model adapts to the user's language (Romanian, English, etc.),
  formality level, verbosity, and technical depth dynamically — no hard-coded
  behaviour, no corporate phrases.

---

## [Unreleased] — 2026-06-04 (patch 9)

### Added

#### Important-info detection and persistence (`supervisor/info_extract.py`)
- `maybe_store_info(mm, message)` — sends the user message to `gpt-4o-mini` with a focused
  extraction prompt (`json_mode=True`). The model returns `{"pairs": {"key": "value", …}}`
  for any names, codes, IDs, dates, preferences, or locations it finds, or `{"pairs": {}}`
  when nothing applies. Extracted pairs are merged into the existing Letta `human` block via
  `mm.set_block("goat", "human", …)` so they become part of the system prompt on the next turn.
- `_merge(existing, new_pairs)` — parses the existing `key: value` block line-by-line and
  overlays new pairs, updating matching keys in-place and appending new ones.

#### `mem_turn` — combined recall + info extraction (`supervisor/mem_inject.py`)
- `mem_turn(mm, intent)` replaces the direct `recall_context` call in `GoatSupervisor.run()`.
  It fans out `recall_context` and `maybe_store_info` concurrently via `asyncio.gather`,
  returning only the `[Memory]` context string. Total per-turn latency is max(recall, extract),
  not their sum.

### Changed

#### `_LIMIT` raised to 20 (`supervisor/mem_inject.py`, `supervisor/session.py`)
- `_LIMIT` in `mem_inject.py` increased from 5 → 20; recall now returns up to 20 entries
  per turn so longer session histories surface more context.
- `_LIMIT: Final[int] = 20` added to `session.py` for consistency.

---

## [Unreleased] — 2026-06-04 (patch 8)

### Added

#### Memory read-back on every turn (`supervisor/mem_inject.py`)
- `recall_context(mm, query)` — fans out `mm.recall("user_session", query, limit=5)`
  across WORKING + EPISODIC + LONG_TERM concurrently. Returns `"[Memory]\n- …"` or `""`
  when nothing is found or `mm` is None. Exceptions are silenced so a dead Letta server
  never blocks a turn.

### Changed

#### Memory context injected before every model call (`supervisor/supervisor.py`, `supervisor/identity.py`, `supervisor/history.py`)
- `GoatSupervisor.run()` calls `recall_context(self.memory_manager, intent)` after
  `history.add_user()` and before routing, producing `mem_ctx` used by both paths.
- **Conversational path**: `conv_result → direct_response` now builds
  `[GOAT_SYSTEM+profile+summary, [Memory] system msg, *history.messages]`.
  The recall block is a dedicated second system message so it never displaces
  the GOAT identity or the prior-session summary.
- **Analytical / complex path**: `as_plan_context(intent, profile, mem_ctx)` prepends
  the `[Memory]` block before the user profile and conversation history in `plan_ctx`,
  so the planner, critic, and synthesizer all see the recalled context.
- Dead code removed: the shadowed `plan_ctx = f"[User: …]\n{intent}"` assignment that
  was unconditionally overwritten two lines later has been deleted.

---

## [Unreleased] — 2026-06-04 (patch 7)

### Changed

#### Full conversation history — system prompt carries prior-session summary (`supervisor/identity.py`, `supervisor/history.py`, `supervisor/supervisor.py`)
- `_system_with_profile(profile, summary)` now appends a `"\nPrevious sessions:\n{summary}"`
  block when a prior-session summary is present. System prompt = GOAT identity + user profile
  + prior-session summary; assembled once at session startup, never refreshed per turn.
- `direct_response(messages, profile, summary)` and `conv_result(..., summary, t0)` updated
  to thread the summary through to the system message.
- `supervisor.run()` passes `self._history.summary` (loaded once at startup via
  `load_session_summary`) to `conv_result`.
- `ConversationHistory.as_plan_context(intent, profile)` added: builds the plan-decomposition
  context string — `[User: …] + [Conversation history]\n{last 6 turns} + intent`. Used by
  the ANALYTICAL and COMPLEX paths so the planner sees full in-session conversation context.
- `supervisor.run()` ANALYTICAL/COMPLEX path now calls `as_plan_context` instead of building
  `plan_ctx` from profile + bare intent.

#### Trigger phrases removed from `GOAT_SYSTEM` (`supervisor/identity.py`)
- Removed: "When [Memory recall] or [Prior context] sections appear in your context, treat
  them as your actual memory…". These were artifacts of the old per-turn injection approach
  and are irrelevant now that the model sees full history directly.

### Removed

#### Per-turn recall module deleted (`supervisor/recall.py`)
- `recall.py` was dead code: `fetch_recall` was no longer called from anywhere since patch 6.
  Deleted to avoid confusion.

#### `load_session_context` removed from `session.py`
- Dead since patch 6 when per-turn recall was replaced by `ConversationHistory`. Only
  `store_turn` remains; `session.py` is now a single-purpose persistence helper.

---

## [Unreleased] — 2026-06-04 (patch 6)

### Added

#### Conversation history (`supervisor/history.py`)
- `ConversationHistory` — maintains `list[{role, content}]` for the current session.
  Seeded at startup with a `[Prior sessions]` system message when a prior-session summary
  exists. Exposes `messages` (snapshot) and `as_context()` (plain-text for DAG injection).
- `load_session_summary(mm)` — retrieves `("user_session", "session_summary")` from
  episodic memory once at startup; returns `""` on first session or Letta unavailable.

### Changed

#### Conversational path uses full message history (`supervisor/identity.py`)
- `direct_response(messages, profile)` — replaces `(intent, profile, session_ctx)`.
  Sends `[GOAT_SYSTEM+profile, *history.messages]` to the model so the LLM sees the
  complete in-session conversation, not a single user turn.
- `conv_result` parameter order updated to match.

#### Per-turn recall injection removed (`supervisor/recall.py`, `supervisor/supervisor.py`)
- `augment_intent` deleted from `recall.py`; `load_session_context` import removed.
  `recall.py` now exports only `fetch_recall` for explicit/manual use.
- `supervisor.py` no longer calls `augment_intent`. On each `run()`:
  - First call: loads profile + session summary, initialises `ConversationHistory`.
  - Every call: `history.add_user(intent)` → classify → route → `history.add_assistant`.
  - DAG paths receive `plan_ctx = "[User: profile]\n{intent}"` (current intent only).

---

## [Unreleased] — 2026-06-04 (patch 5)

### Fixed

#### Memory disclaimer removed from GOAT identity (`supervisor/identity.py`)
- "Nu pot reține informații între sesiuni" and similar disclaimers were generated by
  the underlying LLM from training data because `GOAT_SYSTEM` never told it that memory
  exists. Not a hardcoded string — a prompt gap.
- Added three sentences to `GOAT_SYSTEM`:
  1. "You have persistent memory across sessions." — affirms the capability.
  2. "When [Memory recall] or [Prior context] sections appear in your context, treat them
     as your actual memory and reference them naturally in your answer." — instructs the
     model to use injected recall results rather than ignoring them.
  3. "Never claim you cannot remember previous conversations or lack access to past
     sessions." — explicit prohibition on the disclaimer pattern.

---

## [Unreleased] — 2026-06-04 (patch 4)

### Changed

#### Recall tuning (`supervisor/recall.py`)
- `mm.recall()` now requests exactly 3 entries (`_RECALL_LIMIT = 3`); separate `_RECALL_TOP`
  constant removed — fetch limit and injection limit are the same value.
- Score threshold lowered from `0.5` → `0.3`: broader recall, GOAT retrieves more context
  without requiring high cosine similarity.

---

## [Unreleased] — 2026-06-04 (patch 3)

### Changed

#### Semantic recall replaces trigger-based recall (`supervisor/recall.py`)
- Removed `_TRIGGERS`, `should_recall()`, and the 18-phrase substring-match list.
- `fetch_recall(mm, intent)` now runs unconditionally on every `augment_intent()` call.
- Results are filtered by `metadata["score"] >= 0.5` (cosine similarity). Non-scored
  entries (WORKING, LONG_TERM) always pass — score is only set by ChromaDB searches.
- Top 3 survivors (down from limit=5 candidates) are injected into context.

#### Score propagation from ChromaDB (`memory/chroma_parsers.py`, `memory/types.py`)
- `MemoryEntryMetadata` gains `score: NotRequired[float]` — cosine similarity in [0, 1].
- `_row_to_entry` accepts `*, score: float | None = None`; stores it in metadata when set.
- `_parse_query_result` extracts `distances` from ChromaDB query results and converts:
  `score = max(0.0, 1.0 - distance)` (valid for cosine HNSW space). Entries without a
  distance slot (e.g. padding) get no score and pass the threshold automatically.

---

## [Unreleased] — 2026-06-04 (patch 2)

### Added

#### Active recall (`supervisor/recall.py`)
- `should_recall(intent)` — substring match against 18 trigger phrases in Romanian and
  English (`"ce am zis"`, `"îți amintești"`, `"anterior"`, `"codul"`, `"do you remember"`,
  `"earlier"`, etc.). O(n·m) over short strings; no LLM call.
- `fetch_recall(mm, intent)` — calls `mm.recall("user_session", intent, limit=5)`, which
  fans out concurrently across WORKING + EPISODIC + LONG_TERM and deduplicates by key.
  Returns a `[Memory recall]\n- …` block or `""` when nothing is found.
- `augment_intent(mm, intent, profile)` — single entry point that composes session
  context (`load_session_context`) + user profile + recall (when triggered) into the
  augmented string passed to the planner and all downstream runners.

#### `GoatSupervisor.run()` wired to active recall (`supervisor/supervisor.py`)
- Replaced the 6-line session-ctx + profile augmentation block with one call:
  `augmented = await augment_intent(self.memory_manager, intent, self._user_profile or "")`.
- `load_session_context` import moved from `supervisor.py` to `recall.py`; no external
  API change.

---

## [Unreleased] — 2026-06-04

### Fixed

#### WORKING memory store bypassed Redis (`cli.py`)
- Root cause: `memory_manager.working = WorkingMemoryLayer(backend=b)` replaced the
  attribute on the singleton, but `MemoryCrudMixin._layers` is built once at `__init__`
  and held a stale reference to the original `DictBackend`-backed layer. All routed
  `store(memory_type=WORKING)` calls silently went to `DictBackend`.
- Fix: `memory_manager.working.backend = b` — swaps the backend in-place on the existing
  layer object that `_layers` already references. Removed the now-unused
  `WorkingMemoryLayer` import.

#### `letta_ops_store.do_store` crashed on archival-memory POST response (`memory/letta_ops_store.py`)
- Root cause: Letta 0.16.8 `POST /v1/agents/{id}/archival-memory` returns
  `list[Passage]` (confirmed in `openapi_letta.json`). Code called `data.get("id")`
  directly on the list, raising `AttributeError: 'list' object has no attribute 'get'`.
- Fix: `data = raw[0] if isinstance(raw, list) else raw` — unwraps the first passage
  before field access; falls back to `uuid4` id and `_now_iso()` if the list is empty.

#### `session.store_turn` docstring updated (`supervisor/session.py`)
- Docstring now reflects that turns are written to all three tiers: WORKING, EPISODIC,
  and LONG_TERM.

---

## [Unreleased] — 2026-06-03 (patch 7)

### Fixed

#### Letta core-memory block label alignment
- `letta_registry.py`: renamed `"profile"` block label → `"persona"` (Letta's standard
  agent-persona block). Also gave the `"human"` block the same configurable limit as
  `"persona"` instead of a hardcoded 2048.
- `supervisor/identity.py`: `_PROFILE_KEY` changed from `"user_profile"` → `"human"`
  (Letta's standard user-info block). `get_block("goat", "human")` now matches the label
  actually created by the registry, ending the silent miss on every profile read.

---

## [Unreleased] — 2026-06-03 (patch 6)

### Fixed

#### Letta agent model format (`config/settings.py`)
- `LETTA_LLM_MODEL` default updated to `"openai/gpt-4o-mini"` — Letta 0.16.8 requires
  `provider/model-name` format for the `model` field in `POST /v1/agents/`.

---

## [Unreleased] — 2026-06-03 (patch 5)

### Changed

#### Letta agent creation payload (`memory/letta_registry.py`, `config/settings.py`)
- Added `model` field back to `_create()` payload, set from `cfg.llm_model`.
- `LETTA_LLM_MODEL` default restored to `"gpt-4o-mini"` (plain model name, no provider
  prefix), matching what Letta 0.16.8 accepts for this field.

---

## [Unreleased] — 2026-06-03 (patch 4)

### Fixed

#### Letta agent creation payload (`memory/letta_registry.py`)
- Stripped `_create()` payload to the two fields confirmed required by Letta 0.16.8:
  `name` (string) and `memory_blocks` (array of `{label, value, limit}`).
- Removed `agent_type`, `model`, `embedding`, and `tags` — these caused the 400 Bad
  Request on Letta 0.16.8 and are either rejected or unnecessary when omitted (the
  server applies its own defaults for model and agent type).
- Removed now-unused imports `_GLOBAL_TAG` and `_role_tag` from `letta_helpers`.

---

## [Unreleased] — 2026-06-03 (patch 3)

### Fixed

#### Letta agent creation 400 Bad Request (`memory/letta_registry.py`, `config/settings.py`)
- `_create()` now includes `"model"` and `"embedding"` in the POST `/v1/agents/` payload.
  Letta 0.16.8 replaced the deprecated `llm_config`/`embedding_config` objects with flat
  `model` and `embedding` string handles (`provider/model-name` format). Omitting them
  caused the server to return 400 because no model was bound to the agent.
- Corrected the `LETTA_LLM_MODEL` default from `"gpt-4o-mini"` to `"openai/gpt-4o-mini"`
  to match the required `provider/model-name` format already used by `LETTA_EMBED_MODEL`.
- All other parts of the payload (`name`, `agent_type`, `tags`, `memory_blocks`) were
  verified against the OpenAPI spec (`openapi_letta.json`) and are correct for 0.16.8.

---

## [Unreleased] — 2026-06-03 (patch 2)

### Fixed

#### ChromaDB telemetry noise (`memory/chromadb_base.py`)
- Silenced `chromadb.telemetry.product.posthog` logger at `CRITICAL` level to suppress
  the recurring `"capture() takes 1 positional argument but 3 were given"` errors caused
  by a posthog ↔ chromadb 1.1.1 API mismatch.
- Also passes `Settings(anonymized_telemetry=False)` to `PersistentClient` so the setting
  takes effect in future chromadb versions that honour it.

#### Working-memory backend auto-detection (`cli.py`)
- On startup, `chat_loop` pings `RedisBackend()`. If Redis responds, `memory_manager.working`
  is replaced with a `WorkingMemoryLayer(backend=RedisBackend(...))` before `GoatSupervisor`
  is created, so all working-memory writes for the session use Redis.
- Prints a clear one-line message to stdout:
  - `Working memory: RedisBackend` — Redis is up and in use.
  - `Working memory: DictBackend (Redis unavailable)` — Redis unreachable, in-process dict used.
- The generic `Memory: MemoryManager(...)` banner line has been removed.

---

## [Unreleased] — 2026-06-03

### Added

#### Session persistence (`supervisor/session.py`)
- `store_turn(mm, turn, intent, summary)` — persists each user/assistant exchange to ChromaDB
  episodic memory under role `user_session`, keyed `turn_0001`, `turn_0002`, …
- `load_session_context(mm, query)` — semantic search over prior turns; returns a
  `[Prior context]\n…` block injected into every `run()` call.
- `cli.py` calls `store_turn` after each successful run; on turn 2+ the planner
  receives prior context automatically.

#### GOAT identity and user profile (`supervisor/identity.py`)
- `GOAT_SYSTEM` — personality constant: direct, no-preamble personal assistant.
- `load_user_profile(mm)` — reads `mm.get_block("goat", "user_profile")` on first
  `run()` and caches the result on the `GoatSupervisor` instance. Silently returns `""`
  when Letta is unreachable.
- `direct_response(intent, profile, session_ctx)` — single LLM call (gpt-4o-mini)
  with GOAT identity + user profile injected as system prompt.
- `conv_result(intent, profile, session_ctx, t0)` — wraps `direct_response` into a
  `SupervisorResult(plan=[], results={}, critique="")` for conversational depth.

#### Intent depth classifier (`supervisor/classifier.py`)
- `IntentDepth` enum: `CONVERSATIONAL`, `ANALYTICAL`, `COMPLEX`.
- `classify_intent(intent)` — single gpt-4o-mini call; returns `COMPLEX` on any
  parse failure, so unknown intents always fall through to the full DAG.

#### `cli.py` — interactive chat loop
- Async chat loop that persists `GoatSupervisor` and `memory_manager` across turns.
- Stores each turn to episodic memory via `store_turn` after a successful run.

### Changed

#### `GoatSupervisor` (`supervisor/supervisor.py`)
- `run()` now: loads user profile (lazy) → augments intent with profile + session
  context → classifies depth → routes to conversational / analytical / complex handler.
- `CONVERSATIONAL` depth: bypasses planner and workflow entirely; returns a
  `SupervisorResult` directly from `conv_result`.
- `ANALYTICAL` depth: injects `[Lightweight: ≤2 tasks, no researcher]` hint before
  calling `decompose_plan`; planner respects the constraint.
- `COMPLEX` depth: unchanged full DAG behaviour.
- `_user_profile` cached on instance; only one Letta `get_block` call per session.
- `SupervisorResult.intent` always holds the original (unaugmented) user intent.

#### `supervisor/registry.py`
- Absorbed `_build_default_registry()` and all runner imports from `supervisor.py`,
  keeping `supervisor.py` at ≤ 90 lines while adding new features.

### Architecture notes
- All new files are ≤ 90 lines with single-responsibility design.
- No new runtime dependencies introduced.
- Letta unavailability is handled gracefully in both `load_user_profile` (silent `""`)
  and the memory runner (existing 3-tier fallback).
