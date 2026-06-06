# GOAT 2.0 — Session Notes
**Date:** 2026-06-06  **Branch:** main (no git repo yet)

---

## What was done this session (patch 57)

### Letta human block garbage accumulation — strict ALLOWED_KEYS whitelist

**Root cause:** `info_extract.maybe_store_info` extracted arbitrary key-value pairs from
conversation messages and stored them all in the Letta `human` block. Over time this
accumulated hundreds of irrelevant facts (agent_id, passage_id, search_key, timestamps, etc.)
that corrupted planner context and caused spurious task triggering.

**Fix applied:**

**`supervisor/info_extract.py`**:
- Added `_ALLOWED_KEYS` frozenset whitelist: `name, age, location, city, language, workspace,
  gender, occupation, preferences, rules, canal, device, nationality`.
- Routing logic updated:
  - explicit + whitelisted → PollutionGuard → Letta human block
  - explicit + non-whitelisted → ChromaDB episodic with 7-day TTL
  - inferred + whitelisted → ChromaDB episodic with 7-day TTL
  - inferred + non-whitelisted → discarded entirely
- `_merge()` function updated to filter by ALLOWED_KEYS before overlaying new pairs.
- New `_store_in_chroma()` helper for non-whitelisted explicit facts.

**`memory/pollution_guard.py`**:
- Added same `_ALLOWED_KEYS` frozenset whitelist.
- `validate_fact()` now blocks any key not in ALLOWED_KEYS regardless of kind (explicit/inferred).
- This provides defence-in-depth — even if info_extract sends a non-whitelisted key, guard blocks it.

Both files ≤200 lines with docstrings. All 37 tests pass.

---

## What was done this session (patch 56)

### Planner re-triggering tasks from assistant DAG results

**Root cause:** `as_context()` in `supervisor/history.py` returned both user AND assistant
turns. Assistant turns contain DAG execution results (web search outputs, file reads, memory
queries). The planner saw these as new intent and spawned duplicate tasks.

**Fix applied:**

**`supervisor/history.py`**:
- `as_context()` now filters to user turns only (`m["role"] == "user"`).
- Added new method `as_full_context()` that returns all turns for display/memory only.
- `as_plan_context()` uses `as_context()` (user-only) for planning context.
- Docstrings clarify that assistant results must NOT influence task decomposition.

**`supervisor/planner.py`**:
- `PLANNER_SYSTEM` updated with explicit rules:
  - "Decompose ONLY the current user intent. Ignore previous assistant responses."
  - "Do NOT use prior DAG results (web search, file reads) as input for new tasks."
- These rules prevent duplicate task spawning when planner sees assistant tool outputs.

All 37 tests pass. Both files ≤200 lines with docstrings.

---

## What was done this session (patch 55)

### Telegram token resolution — env var takes precedence over goat.toml

**`supervisor/interfaces/telegram_bot.py`**:
- `_TOKEN` now resolves via: `TELEGRAM_TOKEN` env var → `goat.toml` `[channels].telegram_token` → error.
- Uses `os.environ.get("TELEGRAM_TOKEN")` first, falls back to `load_toml().channel_str("telegram_token")`.
- `build_app()` raises `RuntimeError` with clear message if neither source provides a token.
- Module docstring updated to document the resolution order.
- This allows operators to keep `goat.toml` clean of secrets while using environment variables in production.

All 37 existing tests pass. No imports broken. File remains ≤200 lines with docstrings.

---

## What was done this session (patch 54)

### Three hallucination-prevention fixes + response discipline

All modified files ≤200 lines. 37 tests pass. No imports broken.

**P1 — Empty output after file tool call (`tool_runner.py`)**
Already implemented in a previous patch (lines 92-100). Confirmed present — no change needed.

**P2 — GOAT hallucinates when lacking facts**
`supervisor/supervisor.py`:
- `_unverified_summary` now appends `via {tool_name}` to each failure entry when a tool
  was identified (net_error, empty_file_read cases), e.g. `"researcher via web_search: web
  search returned an error"`. Uses existing `AgentResult.tool_name` field.
- After `synthesize_results`: if summary is empty/whitespace, GOAT sets a factual fallback
  `"Not available. Tools called: {tools}. No output from synthesis."` — no LLM re-call.
`supervisor/runners.py`:
- `_run_summarizer`: guard added before the LLM call — if ALL dep_results have empty
  outputs, returns `"Not available. Upstream tasks returned no output."` immediately.
  Removes the fallback that previously called the LLM with empty context.

**P3 — Response discipline at all times (not just tool failures)**
`supervisor/identity.py`: `GOAT_SYSTEM` updated from `"No filler, no preamble, no sign-offs"`
to `"No filler, no preamble, no apologies, no sign-offs"` — explicit no-apologies rule.
`supervisor/critique.py`: synthesis prompt updated to include `"No apologies."` alongside
the existing no-headers, no-questions rules.

---

## What was done this session (patch 53)

### Four Telegram / DAG safety fixes

All modified files ≤200 lines. New file (content_filter.py) ≤90 lines. 37 tests pass.

**P1 — Empty Telegram message**
`telegram_bot.py`: strip + validate `result.summary` before `reply_text`. If empty →
`"DAG returned empty result. Unverified."` prevents 400 Bad Request from Telegram API.

**P2 — Hallucination on missing file content**
`dag_validator.py`: added `_is_empty_file_read` (source=file + tool_called=True + empty output).
`supervisor.py`: reason `"empty_file_read"` → specific summary `"File read confirmed but content
not received. Unverified."` skips synthesis entirely so GOAT cannot hallucinate file content.

**P3 — Sensitive content leaking to Telegram**
New `supervisor/interfaces/content_filter.py`: `mask_sensitive(text)` two-stage filter.
Stage 1: if ≥2 ALL_CAPS KEY=value lines or sensitive path (.env, api_keys, secrets) detected →
mask ALL env-style key=value pairs (line-start and inline). Stage 2: always mask values next to
credential key names (api_key, secret, password, token, credential, private_key). Applied in
`telegram_bot.py` to every outgoing message before `reply_text`.

**P4 — Missing source label blocks DAG execution**
`task_prep.py`: `prepare_tasks` pre-sets `task.source = "planner"` for tasks with empty source.
`workflow.py`: source-related validation issues now raise `ValueError` (blocking execution) instead
of `log.warning`. Other structural issues continue to warn without blocking.

---

## What was done this session (patch 52)

### DAG source enforcement — block generated on execution tasks

Five interlinked fixes applied to prevent DAG agents from hallucinating results under
`source='generated'`. All modified files ≤200 lines. 37 existing tests pass.

**Root cause:** DAG agents were returning `source='generated'` on execution tasks (researcher,
memory) because the runner had no check after `_call_with_tools`, and the dag_validator only
checked net errors and stale memory — it never validated that execution roles actually called tools.

**Fixes applied:**

- `supervisor/types.py`: `AgentResult` gains `tool_called: bool`, `tool_name: str`,
  `raw_output_hash: str` (all with safe defaults, backward-compatible). `to_dict()` includes them.

- `supervisor/workflow.py`: Populates `tool_called`, `tool_name`, `raw_output_hash` when
  building `AgentResult` after each task completes. `_SOURCE_TOOL` dict maps source → tool name.

- `supervisor/dag_validator.py`: Rewritten with `_EXECUTION_ROLES` (researcher, memory) and
  `_ROLE_ALLOWED_SOURCES` (whitelist per role). `_is_unverified_execution` checks
  `tool_called=False` on execution roles. `_is_source_violation` checks source against whitelist.
  Priority order: `unverified_execution > source_violation > net_error > stale_memory`.

- `supervisor/runners.py`: `_run_researcher` and `_run_tool_caller` raise `RuntimeError` when
  `_call_with_tools` returns `source='generated'` on tasks that required a tool call.

- `supervisor/runner_memory.py`: Tier 3 LLM distillation removed. When no memory is found
  in Tier 1 or Tier 2, runner returns `"ERROR: no memory results"` (never generated content).
  Unused imports cleaned up.

- `supervisor/supervisor.py`: Collects unsafe `val_statuses` from `validate_results`; logs each
  with `task_id` and `reason`; sets `summary = "Unverified"` if any node failed source validation.

---

## What was done last session (patch 49)

### Docstrings added to all tool files (patch 49) — previous session

Every file in `tools/` received a standard English module-level docstring describing
its purpose and primary functionality. Six files modified:

- `tools/__init__.py` — "Tool registry — exports all tool definitions and convenience groupings"
- `tools/calculator.py` — "Safe arithmetic expression evaluator using AST parsing"
- `tools/memory_temporal_tools.py` — "Temporal memory query tools — timeline, recent, and debug trace"
- `tools/memory_tools.py` — "Memory CRUD tools — search, get, and store across memory tiers"
- `tools/think.py` — "Chain-of-thought reasoning tool — records a private reasoning step"
- `tools/web_search.py` — "Web search tool — queries DuckDuckGo instant answers (or custom backend)"

No functional code was changed — only docstrings were added.

### `file_storage_service.py` refactored

Rewritten to under 200 lines by importing shared helpers from `file_storage_helpers.py`:

- `FileStorageService` — abstract base class with `save`/`read`/`read_stream`/`delete`/`exists`/`size`/`list_keys`
- `LocalFileStorage` — filesystem backend with path traversal protection
- `S3FileStorage` — S3-compatible object storage backend (optional)
- `get_storage_backend()` — factory function selecting backend via config/env

Path resolution, error types (`FileStorageError`), and factory logic moved to
`file_storage_helpers.py`.

### Documentation files updated

- **`readme.md`** — created with sections on the tools module, tool inventory (15 tools),
  fixes applied (docstrings, refactor), convenience groups, and security
- **`modul/changelog.md`** — created with entry for patch 49 (docstrings + refactor)
- **`SESSION_NOTES.md`** — updated with this session's work

---

## What works

### Infrastructure
- **Redis auto-detection** — `cli.py` pings Redis on startup; uses `RedisBackend` if up,
  `DictBackend` otherwise. Message printed clearly to stdout.
- **ChromaDB telemetry** — posthog noise suppressed at `CRITICAL` logger level in
  `chromadb_base.py`; `Settings(anonymized_telemetry=False)` also passed for future versions.

### 3-layer memory (`memory/`)
- **Working** — `WorkingMemoryLayer` with `DictBackend` (in-process, TTL 1 h) or
  `RedisBackend` (server-side TTL, drop-in swap).
- **Episodic** — `ChromaMemoryClient` (ChromaDB 1.1.1, cosine HNSW, `all-MiniLM-L6-v2`).
- **Long-term** — `LettaClient` → Letta 0.16.8. Agent creation fixed:
  - Payload: `name` + `model` (`openai/gpt-4o-mini`) + `memory_blocks`.
  - Block labels aligned to Letta defaults: `"persona"` (agent) and `"human"` (user).
  - `get_block("goat", "human")` reads user profile correctly.
  - Graceful fallback to `_InContextFallback` when Letta is unreachable.

### Supervisor (`supervisor/`)
- **Intent classifier** — `classify_intent()` via gpt-4o-mini: routes to
  `CONVERSATIONAL` / `ANALYTICAL` / `COMPLEX` in one cheap LLM call.
- **Conversational** — `direct_response()` with GOAT identity + user profile; no DAG,
  no planner. Response in ~2 s.
- **Analytical** — planner gets `[Lightweight: ≤2 tasks, no researcher]` hint.
- **Complex** — full DAG: planner → wave execution → critique → synthesize.
- **Session persistence** — each turn stored to ChromaDB (`user_session` role);
  loaded and prepended to planner context on next turn.
- **User profile** — lazy-loaded from Letta `"human"` block on first `run()`.

### CLI (`cli.py`)
- Async chat loop, single `GoatSupervisor` instance across turns.
- Clear backend banner on startup (`Working memory: RedisBackend` / `DictBackend`).
- `store_turn()` called after every successful run.

### Tools (`tools/`)
- 15 tool definitions: `THINK`, `CALCULATOR`, `WEB_SEARCH`, `FILE_READ`, `FILE_WRITE`,
  `FILE_CREATE`, `FILE_LIST`, `FILE_SEARCH`, `MEMORY_SEARCH`, `MEMORY_GET`, `MEMORY_STORE`,
  `MEMORY_TIMELINE`, `MEMORY_RECENT`, `MEMORY_DEBUG_TRACE`.
- All file tools share `FileToolExecutor` security gateway.
- All tools have module-level docstrings.

---

## Known limitations
- Letta long-term memory only works when the Letta server is running locally
  (`http://localhost:8283`). Falls back silently otherwise.
- Groq API key not configured — `summarizer` and `critic` runners default to
  `gpt-4o-mini` via env override (`AGENT_SUMMARIZER_MODEL`, `AGENT_CRITIC_MODEL`).
- No persistent git history yet; all changes tracked in `CHANGELOG.md`.
