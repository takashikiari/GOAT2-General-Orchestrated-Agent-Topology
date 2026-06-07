# GOAT 2.0 — Session Notes
**Date:** 2026-06-06  **Branch:** main

---

## What was done this session (patch 67)

### Central roles registry for memory access control

**Architecture:**
- Created `config/roles.py` with `GOAT_ROLE` and `SESSION_ROLE` constants
- All hardcoded role strings centralized in single location
- Prevents role string inconsistencies across codebase

**Implementation:**

**`config/roles.py`** (new):
- `GOAT_ROLE: Final[str] = "goat"` — supervisor identity, persona, profile, behavior
- `SESSION_ROLE: Final[str] = "user_session"` — conversation turns, DAG results, session memory
- Comprehensive docstrings explaining each role's purpose
- Exported in `__all__` for clean imports

**Files refactored to import from config.roles:**
- `supervisor/behavior_store.py` — `_ROLE` → `GOAT_ROLE`
- `supervisor/identity.py` — `_PROFILE_ROLE` → `GOAT_ROLE`
- `supervisor/session.py` — `_ROLE` → `SESSION_ROLE`
- `supervisor/mem_inject.py` — `_ROLE` → `SESSION_ROLE`
- `supervisor/info_extract.py` — `_ROLE` → `GOAT_ROLE`
- `supervisor/history.py` — `_SUMMARY_ROLE` → `SESSION_ROLE`
- `tools/memory_helpers.py` — removed duplicate definitions, imports from config.roles
- `tools/memory_tools.py` — updated imports
- `tools/memory_temporal_tools.py` — updated imports
- `tools/memory_direct_query.py` — updated imports
- `tools/memory_last_write.py` — updated imports
- `supervisor/runner_memory.py` — hardcoded role → `SESSION_ROLE`
- `supervisor/supervisor.py` — `"user_session"` → `SESSION_ROLE` in promote_turns call

**Benefits:**
- Single source of truth for role strings
- Easier to audit memory access patterns
- Prevents typos in role strings
- Simplifies future role additions

**Validation:**
- All files ≤200 lines with docstrings
- No logic changes — only centralization of role strings
- All imports verified working

---

## What was done this session (patch 66)

### Automatic memory promotion pipeline with PollutionGuard validation

**Architecture:**
- **Turn 2+** (messages >= 4): WORKING → EPISODIC, keep_source=True
- **Turn 3+** (messages >= 6): EPISODIC → LONG_TERM, keep_source=False
- **Validation**: PollutionGuard checks content quality before promotion
- **Duplicate detection**: Skips promotion if entry exists in destination tier

**Implementation:**

**`memory/memory_manager.py`**:
- Added `promote_with_guard()` method — checks duplicates, runs PollutionGuard
- Added `promote_turns()` method — background promotion task based on turn count
- Both methods run non-blocking via asyncio.create_task()
- Role namespace: "user_session" for all promotions

**`supervisor/supervisor.py`**:
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

**Validation:**
- All files ≤200 lines with docstrings
- Existing store_turn() and dag_result pipeline unchanged
- Promotion tasks run asynchronously without blocking execution

---

## What was done this session (patch 65)

### Memory binding and tool parameter validation — GOAT vs DAG separation enforced

**Architecture:**
- **GOAT supervisor**: Manages memory read/write directly across all three tiers (Redis, ChromaDB, Letta) with role="goat"
- **DAG agents**: Access tools but restricted to working memory (Redis) only with role="user_session"
- **Validation**: GOAT validates task success by checking tool parameters — never reports validated without verification

**Fixes applied:**

**`supervisor/dag_validator.py`**:
- Added `_is_missing_tool_params()` check — validates tool_called, tool_name, raw_output_hash
- Priority order: missing_tool_params > empty_file_read > unverified_execution
- GOAT cannot mark task safe without verifying tool parameters

**`supervisor/workflow.py`**:
- AgentResult validates tool_called only when tool_name is non-empty
- Logging includes tool_called status in verbose mode

**`supervisor/types.py`**:
- Added `AgentResult.validated` property — checks tool_called AND tool_name AND raw_output_hash
- Added `SupervisorResult.validated` property — all tasks must have verified parameters
- `to_dict()` includes "validated" field for each task

**`tools/memory_tools.py`**:
- `_ROLE = "goat"` for GOAT supervisor full tier access
- Docstrings clarify DAG agents restricted to tier="working" only

**`supervisor/supervisor.py`**:
- Logging includes "validated" status alongside "success"
- `_REASON_LABELS` includes "missing_tool_params" entry

**End-to-end tests:**
- Memory binding verified: GOAT accesses all tiers, DAG restricted to working
- Tool parameter validation verified: tasks without parameters marked unsafe
- All files ≤200 lines with docstrings

---

## What was done this session (patch 64)

### Memory tool binding — GOAT vs DAG separation enforced

**Memory tool access is now strictly separated:**

**GOAT (supervisor/assistant)**:
- Full access to all three memory backends: Redis, ChromaDB, Letta
- Uses `memory_manager` directly with `role="goat"`

**DAG (agents)**:
- Redis read/write only — working memory tier only
- Uses memory tools with `tier="working"` as default

**Implementation**:
- `tools/memory_tools.py`: `_ROLE = "goat"`, `_TIERS = ("working", "episodic", "long_term")`
- `tools/memory_temporal_tools.py`: `_ROLE = "user_session"`, default `tier="working"`
- `supervisor/session.py`: `store_turn()` writes to WORKING tier with `role="user_session"`

All 37 tests pass. All files ≤200 lines with docstrings.

---

## What was done this session (patch 63)

### Tool activation and context synchronization — semantic autonomy, no regex forcing

**Root cause:** `needs_internet()` regex helper in `_run_tool_caller` forced web_search
based on keyword matching. Failed for conversational requests like "Goat! Citește changelogs..."
which require file_read but don't match search keywords.

**Fix applied:**

**`supervisor/runners.py`**:
- Removed `needs_internet()` regex helper entirely
- `_run_tool_caller` now has FULL tool access: FILE_TOOLS + MEMORY_TOOLS + WEB_SEARCH
- System prompt: "Evaluate task semantics to decide which tools are needed"
- `tool_choice='auto'` allows model to select tools based on semantic intent

**`supervisor/supervisor.py`**:
- CONVERSATIONAL path: LLM with CORE_TOOLS — autonomous tool selection
- DAG results bridged into WORKING memory via `store_turn()`

**`supervisor/identity.py`**:
- `direct_response()` always has CORE_TOOLS (MEMORY_TOOLS + FILE_TOOLS)

**Validation:**
- "Goat! Citește changelogs din workspace am reparat tool-urile" triggers file_read autonomously
- All 37 tests pass. All files ≤200 lines with docstrings.

---

## What works

### Infrastructure
- **Redis auto-detection** — cli.py pings Redis on startup
- **ChromaDB telemetry** — posthog noise suppressed

### 3-layer memory
- **Working** — WorkingMemoryLayer with DictBackend or RedisBackend
- **Episodic** — ChromaMemoryClient (ChromaDB 1.1.1, cosine HNSW)
- **Long-term** — LettaClient → Letta 0.16.8 with graceful fallback

### Supervisor
- **Intent classifier** — classify_intent() via gpt-4o-mini (LLM-driven, no keywords)
- **Conversational** — direct_response() with CORE_TOOLS always available
- **Analytical** — planner gets [Lightweight: ≤2 tasks] hint
- **Complex** — full DAG: planner → wave execution → critique → synthesize
- **Session persistence** — turns stored to WORKING, promoted to EPISODIC at session end
- **Tool validation** — GOAT validates parameters before marking tasks successful
- **Automatic promotion** — Turn 2+ to EPISODIC, Turn 3+ to LONG_TERM
- **Central roles registry** — GOAT_ROLE and SESSION_ROLE in config/roles.py

### CLI
- Async chat loop, single GoatSupervisor instance across turns
- store_turn() called after every successful run
- promote_turns() scheduled as background task after store_turn()

### Tools
- 19 tool definitions with module-level docstrings
- All file tools share FileToolExecutor security gateway
- Memory tools: GOAT has full tier access, DAG restricted to working tier only
- All role strings imported from config/roles.py

---

## Known limitations
- Letta long-term memory only works when Letta server is running locally
- Groq API key not configured — summarizer and critic default to gpt-4o-mini
- No persistent git history yet; all changes tracked in CHANGELOG.md
