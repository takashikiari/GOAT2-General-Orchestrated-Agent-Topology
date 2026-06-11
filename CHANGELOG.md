# Changelog

All notable changes to GOAT 2.0 are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased] — 2026-06-11

### Fixed

#### Circular import between `agents/` and `supervisor/` (GOAT-circular-import)

**Problem:**
`agents/base_agent.py` imported `AgentResult, AgentTask` from `supervisor` (top-level), which
triggered `supervisor/__init__.py` → `supervisor/registry.py` → `agents.planner_decompose` →
`supervisor/pipeline` → `tools/` → `agents/base_agent.ToolDefinition` (partially initialized).
Two additional cycles: `planner_decompose` → `supervisor.pipeline.plan_validator` → `supervisor`
→ `registry` → `planner_decompose`; and `agents/critique.py` → `supervisor.identity` →
`supervisor` → `supervisor/supervisor.py` → `agents.critique` (partially initialized).

**Fix applied:**

**`config/agent_types.py`** (new, ~80 lines):
- Moved `AgentRunner`, `TaskStatus`, `AgentTask`, `AgentResult`, `Plan` here from `supervisor/types.py`
- Zero imports from `agents/` or `supervisor/` at runtime — safe as a shared leaf module
- TYPE_CHECKING guards for `MemoryManager` and `ToolDefinition` (no runtime cycle)

**`supervisor/types.py`** (rewritten, ~70 lines):
- Now re-exports all types from `config.agent_types` for full backward compatibility
- Defines `SupervisorResult` (supervisor-specific, stays here)
- All existing `from supervisor.types import X` callers unaffected

**`agents/base_agent.py`, `coder.py`, `critic.py`, `planner.py`, `researcher.py`**:
- Changed `from supervisor import AgentResult, AgentTask` → `from config.agent_types import AgentResult, AgentTask`

**`agents/planner_decompose.py`**:
- Changed `from supervisor.types import AgentTask, AgentResult, Plan` → `from config.agent_types import`
- Moved `from supervisor.pipeline.plan_validator import validate_plan` to lazy import inside `decompose_plan()` — breaks `planner_decompose → supervisor → registry → planner_decompose` cycle

**`agents/critique.py`**:
- Changed `from supervisor.types import AgentResult` → `from config.agent_types import AgentResult`
- Moved `from supervisor.identity import _system_with_profile` to lazy import inside `synthesize_results()` — breaks `critique → supervisor → supervisor.py → critique` cycle

**Validation:**
- `from agents.base_agent import BaseAgent` — no ImportError
- `from agents import BaseAgent, PlannerAgent, ResearcherAgent, CoderAgent, CriticAgent` — no ImportError
- `from supervisor import AgentResult, AgentTask, Plan, SupervisorResult, AgentRegistry` — no ImportError
- `supervisor.types.AgentTask is config.agent_types.AgentTask` — True (single definition, re-exported)
- All 17 tests pass. No functionality changed.

### Added

#### DagBridge + GoatValidator integration (TASKS 1, 2, 5)

**Problem:**
Supervisor used `retrieve_dag_result` (key `dag_result:{id}`) with dag_validator for post-DAG
validation. DagBridge and GoatValidator existed but were not wired into the execution path.

**Changes:**

- `supervisor/pipeline/workflow.py`: DAG now writes result with key `dag:{session_id}:result`
  (TTL 3600s) via `DagBridge.write_result()` instead of `store_dag_result`.

- `supervisor/supervisor.py`:
  - Removed import of `validate_results` from `dag_validator` (replaced by GoatValidator).
  - After `WorkflowGraph.execute()`, uses `DagBridge.wait_for_result(session_id, timeout=120)`
    to poll Redis for `dag:{session_id}:result`.
  - If result found → `validate_dag_result(dag_detail, results)` → `ValidationReport`.
    - `report.passed` → `dag_verified=True`, proceeds to critique + synthesis.
    - `report.failed` → `dag_verified=False`, summary from `ValidationReport.errors`.
  - If result missing (timeout) → `dag_verified=False`, `summary="UNVERIFIED"`.
  - Removed re-validation inside critic fallback loop (was dead computation).
  - `SupervisorResult.critique` now uses `dag_verified` instead of `not (unsafe or missing_src)`.
  - GOAT does NOT invoke tool calls while DAG runs (DAG is awaited sequentially).
  - Pipeline 3 (Memory Promoter) runs via `asyncio.create_task()` after response (unchanged).

#### Tool distribution per role (TASK 3)

**Problem:**
GOAT conversational had file tools it shouldn't use. DAG agents had inconsistent tool access.

**Changes:**

- `supervisor/identity.py` (GOAT CONVERSATIONAL):
  - Tools: 16 memory tools (`registry.memory_tools`) + `WEB_SEARCH`. NO file tools, NO shell.
  - Updated `GOAT_SYSTEM` to list all 16 memory tools; removed file tool references.

- `supervisor/pipeline/runners.py` (DAG agents):
  - `researcher`: `[WEB_SEARCH, MEMORY_SEARCH_DAG]` — web + working-tier memory search.
  - `coder`: 8 file tools + `SHELL` (read-only). Explicit imports; removed `FILE_TOOLS` shim.
  - `critic`: `[MEMORY_RECENT_DAG, MEMORY_GET_DAG]`, `tool_choice="auto"` — read-only context.
    Changed from `_call_llm` to `_call_with_tools`; source propagated via `r.source`.
  - `summarizer`: `[MEMORY_RECENT_DAG]`, `tool_choice="auto"` — read-only recent context.
    Changed from `_call_llm` to `_call_with_tools`; source propagated via `r.source`.
  - `tool_caller`: 8 file tools + 4 DAG memory tools (`dag:*` namespace). No web_search, no shell.
    Explicit tool list replaces `registry.file_tools + registry.dag_memory_tools`.
  - Removed unused `_call_llm` import.

#### config/roles.py prefix constants (TASK 4)

- Added `DAG_PREFIX: Final[str] = "dag"` — Redis namespace for DAG agents.
- Added `GOAT_PREFIX: Final[str] = "goat"` — Redis namespace for GOAT supervisor.
- Updated `__all__` to export both constants.

**Files modified:**
- `supervisor/supervisor.py`
- `supervisor/pipeline/workflow.py`
- `supervisor/pipeline/runners.py`
- `supervisor/identity.py`
- `config/roles.py`

---

## [Unreleased] — 2026-06-10

### Changed

#### Reorganized tools/ into subdirectories

**Problem:**
All tool files were in a flat tools/ directory, making it hard to navigate.

**Changes:**
- Moved file operations to `tools/file/`:
  - file_create.py, file_executor.py, file_executor_helpers.py
  - file_grep.py, file_info.py, file_list.py, file_read.py
  - file_read_lines.py, file_search.py, file_write.py
  - file_storage_helpers.py, file_storage_service.py, path_utils.py
- Moved memory operations to `tools/memory/`:
  - memory_tools.py, memory_helpers.py, memory_temporal_tools.py
  - memory_delete_tool.py, memory_direct_query.py, memory_count_tool.py
  - memory_update_tool.py, memory_promote_tool.py
  - memory_auto_promote_tool.py, memory_embedding_tool.py
  - memory_export_tool.py, memory_last_write.py, memory_ttl_tool.py
- Moved web search to `tools/web/`:
  - web_search.py
- Moved system tools to `tools/system/`:
  - calculator.py, think.py, shell_tool.py
- Created `__init__.py` in each subdirectory
- Updated `tools/__init__.py` to re-export from subdirectories
- Updated all import paths in tool files
- Created `config/tools.py` with tool constants

**Files created:**
- tools/file/__init__.py
- tools/memory/__init__.py
- tools/web/__init__.py
- tools/system/__init__.py
- config/tools.py

**Backward compatibility:**
- All existing imports from `tools import FILE_TOOLS, MEMORY_TOOLS` still work
- tools/__init__.py re-exports everything from subdirectories

---

### Changed

#### Updated memory imports to new module style

**Problem:**
Old import style used nested paths (from memory.memory_manager import MemoryManager).

**Changes:**
- Replaced all imports with new module-style paths:
  - from memory.memory_manager import MemoryManager
  - with: from memory.shared import MemoryManager
- Applied to all supervisor/ files

**Files updated:**
- supervisor/identity.py, supervisor/types.py, supervisor/supervisor.py
- supervisor/behavior/behavior_session.py, behavior_store.py, info_extract.py
- supervisor/pipeline/workflow.py, task_prep.py
- supervisor/session/history.py, session_init.py, session.py, mem_inject.py
- supervisor/tool_runner.py

---

#### Reorganized supervisor/ into subdirectories

**Problem:**
All supervisor modules were in a flat directory structure, making it hard to navigate.

**Changes:**
- Created `supervisor/behavior/` — behavioral learning modules
- Created `supervisor/pipeline/` — DAG execution modules
- Created `supervisor/session/` — session management modules
- Created `supervisor/classification/` — intent classification modules
- Created `supervisor/logging/` — structured logging modules
- Added `__init__.py` to each subdirectory with proper exports
- Updated `supervisor/__init__.py` with re-exports for backward compatibility
- Updated all internal import paths

**New constants:**
- Created `config/supervisor.py` with MAX_WAVES, MAX_TASKS_PER_WAVE, SYNTHESIS_TEMPERATURE

**Documentation:**
- Updated `supervisor/README.md` with full directory structure and module map

**Breaking changes:**
- None — backward compatibility maintained via `supervisor/__init__.py` re-exports

---

## [Unreleased] — 2026-06-08 (patch 72)

### Fixed

#### Expanded Romanian keyword coverage in direct request classifier

**Problem:**
Simple Romanian memory queries like "verifică memoria", "raportează memoria",
or "ce ai în memorie?" were not matched by the direct request classifier,
causing unnecessary full DAG execution.

**Changes in `supervisor/request_classifier.py`:**
- Added patterns for `verifică/verifica/check memoria/memory` — memory check queries
- Added patterns for `arată/afișează/raportează memoria/memory` — show/report memory queries
- Added patterns for `ai/aveți/am în memorie` — "do you have in memory" queries
- All new patterns are case-insensitive and support Romanian diacritics

**Safety:**
- Multi-step indicators (și, analizează, explică) still block bypass correctly
- Queries like "verifică și raportează" still go through DAG (contains "și")

**Documentation:**
- No doc changes needed — README already documents the bypass in Patch 71 section

---

## [Unreleased] — 2026-06-06 (patch 71)

### Added

#### Direct request bypass for simple single-tool queries

**Problem:**
Simple queries like "What's in my recent memory?" or "Read file X" triggered
full DAG execution, wasting resources, adding latency, and diluting answer quality.
Keywords like "verifică", "memorie", "raportează", "analizează" caused the planner
to decompose even trivial requests into multi-agent pipelines.

**Solution:**
Lightweight pre-check classifier identifies single-tool requests before planner
invocation. Uses rule-based pattern matching only — no LLM calls.

**Implementation:**

**`supervisor/request_classifier.py`** (new, 147 lines):
- `DirectRequest` dataclass with is_direct, tool, extracted_param, confidence
- `classify_direct_request()` function with pattern matching
- Pattern categories: memory_recent, memory_get, file_read
- Multi-step indicator rejection (and, explain, analyze, compare, why, how)
- Conservative matching: ambiguous queries always use full DAG
- Supports Romanian and English keywords

**`supervisor/supervisor.py`** (updated, +52 lines):
- Added `_handle_direct_request()` method to GoatSupervisor class
- Pre-check called after intent classification, before planner
- Executes tool directly and returns SupervisorResult
- Falls back to DAG if classification fails or tool execution errors
- Logs bypass events at INFO level with tool name and confidence

**`supervisor/README.md`** (updated):
- Added "Direct Request Bypass (Patch 71)" section
- Documents bypassed tools, classification rules, examples
- Shows logging format for bypass events

**Bypassed tools:**
- `memory_recent` — queries about recent memory items
- `memory_get` — queries retrieving specific named facts  
- `file_read` — queries reading specific files by path

**Safety constraints:**
- Rejects multi-step indicators immediately
- Confidence threshold >= 0.5 required for bypass
- Falls back to DAG on any uncertainty or error
- No changes to planner, DAG validator, or existing tool execution

**Example bypass queries:**
- "What recent memory items do I have?"
- "Show me the last stored fact"
- "Read file config.toml"
- "Ce am în memorie recent?"

**Example DAG queries (not bypassed):**
- "Show me recent changes and explain their impact"
- "Analyze the codebase and suggest refactoring"
- "Compare the two files and tell me which is better"

**Benefits:**
- Reduced latency for simple queries (no DAG overhead)
- Lower resource consumption (no multi-agent pipeline)
- Improved answer quality (direct tool output, no synthesis)
- Conservative safety (ambiguous queries use full DAG)

**Documentation:**
- All files ≤200 lines with docstrings
- No changes to memory promotion, tool execution, or Telegram interface
- Existing DAG validator, contradiction detection, model fallback preserved

---

## [Unreleased] — 2026-06-06 (patch 70)

### Fixed

#### Dynamic model fallback and contradiction detection in DAG validator

**Problem 1: Hard-coded model fallback**
Previously, when the planner or any DAG agent encountered an unavailable model,
the code fell back to a fixed model (e.g., "gpt-4o"). This was undesirable because:
- It ignored the user's configured model preferences.
- It could cause incoherent outputs because the fallback model's style may clash with other agents.
- It made the system fragile: if that one model was also unavailable, everything failed.

**Problem 2: No contradiction cross-check in validator**
The DAG validator already checked for tool-parameter completeness, empty outputs,
unverified executions, etc. However, it did **not** detect contradictory information
produced by different agents. As a result, a DAG could be marked `validated=True`
even when two agents made mutually exclusive claims about the same fact.

**Solution:**

**`config/model_selector.py`** (new, 147 lines):
- Added `ModelSelector` helper with configurable priority lists per role.
- `get_model_for_role(role)` returns first available model from priority list.
- Health checks validate API key presence before selecting model.
- Raises `ModelUnavailableError` if all models fail (no silent fallback).
- Supports goat.toml configuration: `[agents.{role}].models = [...]`
- Backward-compatible with single model: `[agents].{role} = "model-key"`
- Environment variable override: `AGENT_{ROLE}_MODEL`

**`supervisor/dag_validator.py`** (updated, +67 lines):
- Added `_CONTRADICTIONS` dict with semantic opposites (true/false, yes/no, etc.).
- Added `_extract_claims()` helper to extract claims from text.
- Added `_is_contradictory()` function to detect conflicts between result pairs.
- Inserted contradiction check into validation priority chain (after `empty_generated`, before `unverified_execution`).
- Logs conflicting task IDs and claim snippets at WARNING level.
- Marks both conflicting tasks as `safe=False` when contradiction found.

**`supervisor/README.md`** (updated):
- Added "Dynamic Model Fallback (Patch 70)" section documenting configuration.
- Added "Contradiction Detection (Patch 70)" section with validation priority list.
- Includes goat.toml examples and environment variable override instructions.

**Configuration example (goat.toml):**
```toml
# Preferred: list of models in priority order
[agents.planner]
models = ["deepseek-r1", "gpt-4o", "llama-3.3-70b"]

# Backward-compatible: single model
[agents]
researcher = "deepseek-chat"
```

**Benefits:**
- Respects user model preferences instead of hard-coded fallbacks.
- Clear error when no models available (no silent degradation).
- Catches contradictory agent outputs before synthesis.
- Prevents DAG validation when agents disagree on critical facts.

**Documentation:**
- All files ≤200 lines with docstrings.
- No changes to memory promotion, tool execution, file I/O, or Telegram interface.
- Existing validation checks preserved with same priority relative to each other.

---

## [Unreleased] — 2026-06-06 (patch 69)

### Fixed

#### DAG dependency validation — planner validates depends_on references and cycles

**Problem:**
The planner had a fallback for malformed JSON, but no fallback for structurally
valid JSON that contains logical errors — specifically, tasks that `depends_on`
IDs not present in the plan, or dependency chains that create cycles.

**Solution:**
Added dependency validation in planner with automatic repair and fallback mechanisms.

**Implementation:**

**`supervisor/planner.py`** (updated):
- Added `_validate_plan_dependencies()` pure function:
  - Checks every `depends_on` entry references a real task ID in the plan.
  - Detects cycles using DFS (WHITE/GRAY/BLACK coloring algorithm).
  - Returns `(is_valid, error_message)` tuple.
- Added `_strip_invalid_dependencies()` helper:
  - Removes invalid `depends_on` references from tasks.
  - Creates new AgentTask objects with cleaned dependencies.
  - Logs stripped dependencies at DEBUG level.
- Updated `decompose_plan()`:
  - Validates dependency integrity after JSON extraction.
  - Attempts automatic repair by stripping invalid dependencies.
  - Falls back to minimal 2-task plan if repair fails (cycle detected).
  - All validation failures logged at WARNING level with specific error details.

**`supervisor/workflow.py`** (updated):
- Added `ReplanCallback` type alias for optional replanning callback.
- Updated `WorkflowGraph.execute()`:
  - Added `replan_callback` parameter (default `None`).
  - Added `original_intent` parameter for replanning context.
  - ValidationError during topological sort logged at WARNING level.
  - If `replan_callback` provided, invokes callback to attempt replanning.
  - Successful replan rebuilds DAG and retries execution.
  - Failed DAGs return empty results dict with error logged.

**`supervisor/README.md`** (updated):
- Added "DAG Dependency Validation" section documenting validation checks.
- Documents recovery strategy (automatic repair → fallback plan).
- Lists example validation failures.

**Benefits:**
- Planner LLM hallucinations no longer crash the entire DAG.
- Invalid dependencies automatically repaired when possible.
- Clear logging for debugging planner issues.
- Optional replanning callback enables supervisor-driven recovery.

**Documentation:**
- All files ≤200 lines with docstrings.
- No changes to dag_validator.py (validation is planner-side, not post-execution).
- Memory promotion, tool execution, file I/O, Telegram interface unchanged.

---

## [Unreleased] — 2026-06-06 (patch 68)

### Added

#### Centralize magic strings and constants into config modules

**Architecture:**
- Created `config/tiers.py` with memory tier constants
- Created `config/limits.py` with numeric limits and TTL values
- Created `config/timeouts.py` with timeout constants
- All hardcoded values centralized for easier maintenance

**Implementation:**

**`config/tiers.py`** (new):
- `WORKING: Final[str] = "working"` — working memory tier
- `EPISODIC: Final[str] = "episodic"` — ChromaDB semantic search tier
- `LONG_TERM: Final[str] = "long_term"` — Letta core memory tier
- `ANY: Final[str] = "any"` — search across all tiers
- Comprehensive docstrings explaining each tier's purpose

**`config/limits.py`** (new):
- `MAX_LINES_PER_FILE: Final[int] = 200` — file read line limit
- `MAX_RECALL_LIMIT: Final[int] = 50` — memory recall max entries
- `MAX_TURNS_HISTORY: Final[int] = 20` — conversation turn limit
- `DAG_RESULT_TTL: Final[int] = 3600` — DAG result TTL (1 hour)
- `WORKING_MEMORY_TTL: Final[int] = 3600` — default working memory TTL
- `INFERRED_MEMORY_TTL: Final[int] = 604800` — inferred facts TTL (7 days)

**`config/timeouts.py`** (new):
- `TURN_TIMEOUT: Final[int] = 120` — conversation turn timeout
- `TOOL_TIMEOUT: Final[int] = 30` — tool execution timeout
- `LETTA_TIMEOUT: Final[int] = 8` — Letta HTTP timeout
- `REDIS_TIMEOUT: Final[int] = 5` — Redis connection timeout

**Files refactored to import from config modules:**
- `memory/working_crud.py` — TTL values → `WORKING_MEMORY_TTL`
- `memory/letta_ops_retrieve.py` — limits → `MAX_RECALL_LIMIT`, timeouts → `LETTA_TIMEOUT`
- `supervisor/session.py` — TTL → `DAG_RESULT_TTL`, tier → `WORKING`
- `tools/memory_temporal_tools.py` — tier strings → `ANY` from config.tiers
- `tools/memory_helpers.py` — tier constants → imports from config.tiers
- `supervisor/runner_memory.py` — timeout → `LETTA_TIMEOUT`

**Benefits:**
- Single source of truth for all constants
- Easier to tune system behavior
- Prevents magic number inconsistencies
- Simplifies configuration changes

**Documentation:**
- All files ≤200 lines with docstrings
- No logic changes — only centralization of constants

---

## [Unreleased] — 2026-06-06 (patch 67)

### Added

#### Central roles registry for memory access control

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

**Documentation:**
- All files ≤200 lines with docstrings
- No logic changes — only centralization of role strings

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
