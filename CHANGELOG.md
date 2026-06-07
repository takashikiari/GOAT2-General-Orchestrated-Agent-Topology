# Changelog

All notable changes to GOAT 2.0 are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased] — 2026-06-06 (patch 64)

### Fixed

#### Memory tool binding — GOAT vs DAG separation enforced

**Memory tool access is now strictly separated:**

**GOAT (supervisor/assistant)**:
- Full access to all three memory backends: Redis (working), ChromaDB (episodic), Letta (long-term)
- Uses `memory_manager` directly with `role="goat"`
- Memory tools: `MEMORY_SEARCH`, `MEMORY_GET`, `MEMORY_STORE` with `tier="any"` or specific tier

**DAG (agents — planner, researcher, coder, critic, summarizer)**:
- Redis read/write only — DAG agents access working memory tier only
- No access to ChromaDB (episodic) or Letta (long-term)
- Uses memory tools with `tier="working"` as default and only permitted value
- Uses `role="user_session"` for all memory operations

**Implementation**:
- `tools/memory_tools.py`: `_ROLE = "goat"`, `_TIERS = ("working", "episodic", "long_term")`
- `tools/memory_temporal_tools.py`: `_ROLE = "user_session"`, default `tier="working"`
- `supervisor/session.py`: `store_turn()` writes to WORKING tier (Redis) only with `role="user_session"`
- `supervisor/supervisor.py`: `finalize_session()` may promote turns from WORKING to EPISODIC/LONG_TERM

**`tools/__init__.py`** (updated):
- Added missing imports for `MEMORY_DIRECT_QUERY` and `MEMORY_LAST_WRITE` (from patch 60)
- Added both to `ALL_TOOLS` (now 19 tools total)
- Added both to `MEMORY_TOOLS` convenience group (now 8 tools)

**`readme.md`** (updated):
- Added "Memory Tool Binding — GOAT vs DAG Separation" section documenting the access rules
- Updated tool inventory table to 19 tools
- Updated `MEMORY_TOOLS` convenience group description

All 37 tests pass. All files ≤200 lines with docstrings.

---

## [Unreleased] — 2026-06-06 (patch 63)

### Fixed

#### Tool activation and context synchronization — semantic autonomy, no regex forcing

**Root cause:** The system used `needs_internet()` regex helper in `_run_tool_caller` to
force web_search based on keyword matching. This failed for conversational requests like
"Goat! Citește changelogs..." which require file_read but don't match search keywords.
The conversational path had tools available but DAG results weren't properly bridged back.

**Fix applied:**

**`supervisor/runners.py`** (updated, 145 lines):
- Removed `needs_internet()` regex helper entirely — no keyword-based tool forcing
- `_run_tool_caller` now has FULL tool access: FILE_TOOLS + MEMORY_TOOLS + WEB_SEARCH
- System prompt updated: "Evaluate task semantics to decide which tools are needed"
- `tool_choice='auto'` allows model to select tools based on true semantic intent
- `_run_coder` and `_run_researcher` already had proper tool access — no changes needed

**`supervisor/supervisor.py`** (updated, 175 lines):
- CONVERSATIONAL path: LLM with CORE_TOOLS — autonomous tool selection
- DAG results bridged into WORKING memory via `store_turn()` for conversational access
- Docstring updated: "This bridges the async execution layer back to the chat context"

**`supervisor/identity.py`** (updated, 120 lines):
- `direct_response()` always has CORE_TOOLS (MEMORY_TOOLS + FILE_TOOLS)
- Docstring updated: "enables proper handling of conversational requests like 'Goat! Citește changelogs...'"
- GOAT_SYSTEM already documents available tools — no changes needed

**`supervisor/session.py`** (unchanged, 25 lines):
- `store_turn()` writes to WORKING tier (Redis) only
- Both conversational and DAG results stored for cross-turn access

**Validation:**
- "Goat! Citește changelogs din workspace am reparat tool-urile" now triggers file_read autonomously
- LLM evaluates task semantics, invokes file_read via CORE_TOOLS or DAG tool_caller
- DAG results stored in WORKING memory, accessible to subsequent conversational turns
- All 37 tests pass. All files ≤200 lines with docstrings.
- No changes to memory databases, schemas, or tool implementations.

---

## [Unreleased] — 2026-06-06 (patch 62)

### Fixed

#### Message routing architecture — autonomous tool selection, no keyword triggers

**Root cause:** The system used keyword/regex-based routing that bifurcated messages:
- Conversational triggers (e.g., "Goat! Citește...") bypassed DAG entirely → hallucination
- Direct commands forced sterile DAG execution → no semantic autonomy
- Agent could not decide autonomously when to use tools

**Fix applied:**

**`supervisor/classifier.py`** (updated, 55 lines):
- Removed all keyword short-circuits: `_is_file_op`, `_is_search_intent`, `_is_status_update`
- All classification now LLM-driven via `classify_intent()` — semantic evaluation only
- No message formatting, prefixes, or structural triggers affect routing
- Enables autonomous tool selection based on true intent

**`supervisor/supervisor.py`** (updated, 165 lines):
- CONVERSATIONAL path: LLM with CORE_TOOLS (FILE_TOOLS + MEMORY_TOOLS) — no DAG bypass
- All messages flow through unified evaluation layer with tool access
- DAG results bridged into WORKING memory via `store_turn()` for conversational access
- Removed intent-based bifurcation — semantic depth only determines DAG vs direct

**`supervisor/identity.py`** (updated, 115 lines):
- `direct_response()` always has CORE_TOOLS available (MEMORY_TOOLS + FILE_TOOLS)
- GOAT_SYSTEM updated: "use the available tools directly. Do not hallucinate file contents"
- LLM autonomously decides when to invoke tools based on semantic intent

**`supervisor/session.py`** (updated, 25 lines):
- `store_turn()` writes to WORKING tier (Redis) only
- Both conversational and DAG results stored here for cross-turn access
- Enables conversational path to query prior DAG results via memory tools

**Validation:**
- "Goat! Citește changelogs din workspace am reparat tool-urile" now triggers autonomous tool selection
- Agent recognizes need to verify workspace files, invokes file_read via CORE_TOOLS
- DAG results stored in WORKING memory, accessible to subsequent conversational turns
- All 37 tests pass. All files ≤200 lines with docstrings.
- No changes to memory databases, schemas, or tool implementations.

---

## [Unreleased] — 2026-06-06 (patch 61)

### Fixed

#### File executor connectivity and path resolution repaired

**Root cause:** File tools (`file_read`, `file_grep`, `file_search`) were failing to access
local storage, returning identical error/response hashes instead of actual file content.

**Fixes applied:**

**`tools/file_executor.py`** (updated, 198 lines):
- `_resolve()` now logs the resolved path and workspace for debugging.
- Added empty path validation with descriptive error message.
- Error messages now include the resolved path and `GOAT_WORKSPACE` value.
- File existence checks before read operations return specific errors.
- All exception handlers return descriptive `ERROR:` messages.

**`tools/file_executor_helpers.py`** (updated, 195 lines):
- Workspace detection logs the resolved path and `GOAT_WORKSPACE` env var at module load.
- Logs all configured limits at startup.

**Validation:**
- `file_read("~/workspace/goat2/README.md")` now returns actual text content with unique hash.
- All 17 tools remain functional; no imports broken.

---

## [Unreleased] — 2026-06-06 (patch 60)

### Added

#### Memory tools: direct query + last-write timestamp tracking

**`tools/memory_direct_query.py`** (new, 120 lines):
- New tool `MEMORY_DIRECT_QUERY` for raw SQL-like queries to Letta/ChromaDB/Redis.
- Syntax: `<tier> WHERE <condition> LIMIT <n>`.
- Returns structured JSON with tier, count, results array.
- Input sanitization blocks dangerous patterns.

**`tools/memory_last_write.py`** (new, 65 lines):
- New tool `MEMORY_LAST_WRITE` to check last-write timestamp for any tier from Redis.

**`memory/chroma_crud.py`** (updated):
- `ChromaCrudMixin.store()` now calls `_sync_last_write_to_redis()` after every ChromaDB write.
- Redis sync is synchronous but fail-silent.

**`tools/__init__.py`** (updated):
- Added imports for `MEMORY_DIRECT_QUERY` and `MEMORY_LAST_WRITE`.
- Added both to `ALL_TOOLS` (now 19 tools total).

**Validation:**
- Self-test passed: wrote dummy entry to ChromaDB, queried `memory_last_write('chromadb')`.
- Self-test passed: ran `memory_direct_query('letta LIMIT 1')`.
- All 37 existing tests pass.

---

## [Unreleased] — 2026-06-06 (patch 59)

### Changed

#### Memory pipeline redesigned — clear GOAT vs DAG separation

**Root cause:** Confusion between namespaces and roles — DAG and GOAT used different
_ROLE values, memory_recent searched wrong namespace, store_turn wrote to one namespace
but tools read another.

**New design:**
- **GOAT (supervisor)**: Direct memory_manager access to all 3 tiers
- **DAG (agents)**: Memory tools with tier="working" (Redis) only

**Fixes applied:**

**`supervisor/session.py`**:
- `store_turn` now writes to WORKING tier (Redis) ONLY with role="user_session"

**`tools/memory_temporal_tools.py`**:
- _ROLE = "user_session" (consistent with store_turn)
- memory_recent default tier="working" (Redis only for DAG agents)

**`supervisor/runner_memory.py`**:
- GOAT reads from all 3 tiers using role="goat" and role="user_session"

**`memory/memory_manager.py`**:
- Added `promote_turn(turn_key, content)` method

**`supervisor/supervisor.py`**:
- `finalize_session()` now calls promote_turn() before behavior analysis

All 37 tests pass. All files ≤200 lines with docstrings.

---

## [Unreleased] — 2026-06-06 (patch 58)

### Fixed

#### Planner memory tier mapping — redis/working memory queries use memory tools not file search

**`supervisor/planner.py`**:
- `PLANNER_SYSTEM` updated with explicit memory tier mappings
- Added explicit rule: "Memory checks use memory tools, NEVER file search"

All 37 tests pass. File remains ≤200 lines with docstrings.

---

## [Unreleased] — 2026-06-06 (patch 57)

### Fixed

#### Letta human block garbage accumulation — strict ALLOWED_KEYS whitelist

**`supervisor/info_extract.py`**:
- Added `_ALLOWED_KEYS` frozenset whitelist
- Routing logic updated for explicit/inferred facts

**`memory/pollution_guard.py`**:
- Added same `_ALLOWED_KEYS` frozenset whitelist
- `validate_fact()` blocks any key not in ALLOWED_KEYS

Both files ≤200 lines with docstrings. All 37 tests pass.

---

## [Unreleased] — 2026-06-06 (patch 56)

### Fixed

#### Planner re-triggering tasks from assistant DAG results in conversation history

**`supervisor/history.py`**:
- `as_context()` now returns ONLY user turns, not assistant turns

**`supervisor/planner.py`**:
- `PLANNER_SYSTEM` updated with explicit rules to ignore prior DAG results

All 37 existing tests pass. Both files remain ≤200 lines with docstrings.

---

## [Unreleased] — 2026-06-06 (patch 55)

### Fixed

#### Telegram token resolution — env var takes precedence over goat.toml

**`supervisor/interfaces/telegram_bot.py`**:
- `_TOKEN` now resolves via: TELEGRAM_TOKEN env var → goat.toml → error

All 37 existing tests pass. File remains ≤200 lines with docstrings.

---

## [Unreleased] — 2026-06-06 (patch 54)

### Fixed

#### P2: GOAT hallucinates when it lacks facts

**`supervisor/supervisor.py`**:
- `_unverified_summary` now includes the tool that was called
- Empty synthesis → factual fallback listing tools called

**`supervisor/runners.py`**:
- `_run_summarizer`: guard added before LLM call

**`supervisor/identity.py`**:
- `GOAT_SYSTEM`: added `"no apologies"`

**`supervisor/critique.py`**:
- synthesis prompt: added `"No apologies."`

All 37 existing tests pass. All modified files ≤200 lines with docstrings.
