# Changelog

All notable changes to GOAT 2.0 are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased] — 2026-06-06 (patch 66)

### Added

#### Automatic memory promotion pipeline with PollutionGuard validation

**Architecture:**
- **Turn 2+** (messages >= 4): WORKING → EPISODIC, keep_source=True
- **Turn 3+** (messages >= 6): EPISODIC → LONG_TERM, keep_source=False
- **Validation**: PollutionGuard checks content quality before promotion
- **Duplicate detection**: Skips promotion if entry exists in destination tier

**Implementation:**

**`memory/memory_manager.py`** (updated):
- Added `promote_with_guard()` method — checks duplicates, runs PollutionGuard
- Added `promote_turns()` method — background promotion task based on turn count
- Both methods run non-blocking via asyncio.create_task()
- Role namespace: "user_session" for all promotions

**`supervisor/supervisor.py`** (updated):
- Added `_schedule_promotion()` helper method
- After store_turn() in CONVERSATIONAL branch: schedules promotion task
- After store_turn() in DAG branch: schedules promotion task
- Promotion runs as background task (non-blocking)
- Errors logged as warnings (non-critical)

**Promotion rules:**
- Duplicate detection: Checks destination tier before promoting
- PollutionGuard: Validates content quality, blocks garbage accumulation
- Keep source: WORKING→EPISODIC keeps source, EPISODIC→LONG_TERM deletes source
- Turn thresholds: Turn 2+ for episodic, Turn 3+ for long_term

**Documentation:**
- Module docstrings updated with promotion pipeline details
- Architecture diagram updated to show automatic promotion flow
- All files ≤200 lines with docstrings

---

## [Unreleased] — 2026-06-06 (patch 65)

### Fixed

#### Memory binding and tool parameter validation — GOAT vs DAG separation enforced

**Architecture:**
- **GOAT supervisor**: Manages memory read/write directly across all three tiers (Redis, ChromaDB, Letta) with role="goat"
- **DAG agents**: Access tools but restricted to working memory (Redis) only with role="user_session"
- **Validation**: GOAT validates task success by checking tool parameters — never reports validated without verification

**Fixes applied:**

**`supervisor/dag_validator.py`** (updated, 115 lines):
- Added `_is_missing_tool_params()` check — validates tool_called, tool_name, raw_output_hash
- Priority order: missing_tool_params > empty_file_read > unverified_execution > source_violation
- GOAT cannot mark task safe without verifying tool parameters were present
- Module docstring updated to document parameter validation requirement

**`supervisor/workflow.py`** (updated, 105 lines):
- AgentResult now validates tool_called only when tool_name is non-empty
- Added logging for tool_called status in verbose mode
- Module docstring updated to document GOAT/DAG memory separation

**`supervisor/types.py`** (updated, 125 lines):
- Added `AgentResult.validated` property — checks tool_called AND tool_name AND raw_output_hash
- Added `SupervisorResult.validated` property — all tasks must have verified parameters
- `to_dict()` now includes "validated" field for each task
- Module docstring updated to document validation requirements

**`tools/memory_tools.py`** (updated, 95 lines):
- `_ROLE = "goat"` for GOAT supervisor full tier access
- Docstrings clarify DAG agents restricted to tier="working" only
- Parameter descriptions note GOAT vs DAG access differences

**`supervisor/supervisor.py`** (updated, 185 lines):
- Logging now includes "validated" status alongside "success"
- `_REASON_LABELS` includes "missing_tool_params" entry
- Module docstring documents GOAT memory management vs DAG restrictions

**Documentation:**
- All files remain ≤200 lines with docstrings
- End-to-end tests verify memory binding and parameter validation

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
