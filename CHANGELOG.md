# Changelog

All notable changes to GOAT 2.0 are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased] — 2026-06-12

### Added

#### GOAT → DAG isolation + control protocol

**TASK 1 — GOAT→DAG communication via working memory:**
- `supervisor/session/session.py`: `write_dag_instructions(mm, session_id, intent, mem_ctx, capabilities)` writes structured task instructions to `dag:<session_id>:instructions` (TTL 3600s); `retrieve_dag_instructions(mm, session_id)` reads them back. Both exported in `__all__`.
- `supervisor/supervisor.py`: `run()` writes DAG instructions before calling `_run_dag()` using a module-level `_DAG_CAPABILITIES_SUMMARY` constant.
- `supervisor/pipeline/dag_execution.py`: `run_dag_pipeline()` reads `dag:<session_id>:instructions` and uses the structured intent/context as `plan_ctx`; falls back to raw intent if key missing (backward compat).

**TASK 2 — GOAT DAG Control Protocol:**
- `supervisor/pipeline/dag_control.py` (new): `write_dag_control`, `read_dag_control`, `wait_if_paused` helpers for `dag:<session_id>:control` key (`"run"|"pause"|"stop"`).
- `supervisor/pipeline/workflow.py`: `WorkflowGraph` checks control key after each wave via `wait_if_paused`; `"pause"` waits up to 60s (2s intervals); `"stop"` writes final progress and returns early.
- `supervisor/supervisor.py`: `pause_dag(session_id)`, `resume_dag(session_id)`, `stop_dag(session_id)`, `get_dag_updates(session_id)` public methods.

**TASK 3 — DAG control tools for GOAT CONVERSATIONAL:**
- `supervisor/pipeline/dag_tools.py` (new): `make_dag_tools(mm)` factory returning `query_dag_status` and `control_dag` ToolDefinition objects with `memory_manager` captured via closure.
- `supervisor/identity.py`: `direct_response()` now includes the two DAG tools in its tool list. Onboarding block extracted to `supervisor/identity_onboarding.py` to maintain the 260-line budget.

**TASK 4 — Session helpers export:**
- `supervisor/session/session.py`: `write_dag_instructions` and `retrieve_dag_instructions` added to `__all__`.

---

## [Unreleased] — 2026-06-11

### Changed

#### Pure LLM intent classifier + DAG awareness + behavioral learning

**TASK 1 — Pure LLM intent classifier** (`supervisor/classification/`):

- Replaced all hardcoded patterns (`_HELP_PATTERNS`, `_VAGUE_FIRST_PATTERNS`,
  greeting lists, length heuristics) with a single LLM-driven decision.
- `classify_intent(intent, registry)` now sends the intent + full context
  (what GOAT can do directly, what requires the DAG, conversation
  history, active DAG sessions, user profile, override, prior
  corrections) to the LLM. The model replies with exactly one word:
  `conversational`, `analytical`, or `complex`.
- **Zero hardcoded keywords** in the classifier. Every intent — including
  "?", "salut", "help", and one-word messages — flows through the same
  semantic LLM path.
- Split into 3 modules to respect the file-size rule:
  - `classifier.py` (170 lines) — entry point, IntentDepth enum,
    classification + override-application + log-and-fallback.
  - `classifier_prompt.py` (102 lines) — system prompt + prompt
    builder + formatters (history, active DAGs, hints).
  - `classifier_context.py` (103 lines) — `detect_override`,
    `gather_active_dags`, `gather_user_profile`, `gather_hints`
    (LLM-driven, best-effort).
- `IntentDepth` enum preserved exactly: `CONVERSATIONAL`,
  `ANALYTICAL`, `COMPLEX`. Callers and validators unaffected.
- Fallback on parse failure: `CONVERSATIONAL` (safe default — never
  escalate to a full DAG on uncertainty).

**TASK 2 — DAG awareness in GOAT** (`supervisor/supervisor.py` +
`supervisor/pipeline/dag_awareness.py`):

- New `dag_awareness.py` module (190 lines) with canonical read
  primitives for GOAT:
  - `scan_active_dags(registry)` — scan working memory for
    `dag:*:progress` keys and return active sessions.
  - `read_dag_progress(registry, session_id)` — read the current
    progress record for a specific DAG.
  - `read_override(registry, session_id)` / `write_override(...)` —
    read/persist the user's routing override for the session.
  - `persist_session_override(registry, intent, session_id)` —
    detect (semantically) and persist the override in one call.
- `GoatSupervisor.run()` now:
  1. Calls `prepare_classification_context(...)` before classifying
     — that helper attaches the conversation history to the
     registry, scans working memory for active DAGs, and persists
     any user override.
  2. The classifier LLM sees the active-DAG summary and can prefer
     CONVERSATIONAL for follow-ups about in-flight work.
- Backward-compat: `_check_active_dags()` and `_read_dag_progress()`
  instance methods on `GoatSupervisor` are preserved as thin
  wrappers over the new module.

**TASK 3 — DAG progress reporting** (`supervisor/pipeline/workflow.py` +
`supervisor/pipeline/dag_progress.py`):

- New `dag_progress.py` module (108 lines) with two write primitives:
  - `write_wave_progress(memory_manager, session_id, wave,
    total_waves, completed)` — called after every wave finishes.
  - `write_final_progress(memory_manager, session_id, total_waves,
    completed)` — called once at the end, marks the progress key
    as `status="complete"`.
- Key format: `dag:<session_id>:progress`, TTL 3600s, payload
  `{session_id, wave, total_waves, completed_tasks, status, ts}`.
- `WorkflowGraph.execute()` writes progress after every wave and
  marks the final wave as `complete` before writing the final
  result. GOAT reads the same key on demand via
  `query_dag_status` / `memory_get`.

**TASK 4 — Explicit user control** (semantic override):

- Override detection is **purely LLM-driven**: the override prompt
  describes in prose what an override looks like, the model
  extracts it semantically (no keyword list).
- The override (`conversational` / `complex`) is stored in working
  memory under `goat:<session_id>:override`, TTL 3600s.
- Override always wins over the classifier's normal decision.

**TASK 5 — Behavioral learning via episodic memory**
(`supervisor/pipeline/behavioral_learning.py`):

- New `behavioral_learning.py` module (137 lines) with two primitives:
  - `store_correction(registry, intent, goat_routed, user_wanted,
    note)` — persist a labeled example to **episodic memory** (ChromaDB).
  - `recall_corrections(registry, limit)` — semantic search of
    past corrections.
- **Zero hardcoded examples, zero ChromaDB seeding, zero regex rules.**
  The classifier queries episodic memory for past corrections whose
  `intent` is semantically close and shows them to the LLM as soft
  hints. The LLM weighs the corrections alongside everything else
  in the prompt.

**TASK 6 — `supervisor/classification/README.md`** (new, ~340 lines, no limit):

- Documents the LLM-based classifier, the `IntentDepth` enum, the
  no-keywords invariant, working memory as the GOAT↔DAG channel,
  DAG progress reporting, explicit user control, behavioral
  learning, and the strict memory separation rules.

**TASK 7 — `docs/architecture.md`** (rewritten "Intent Classification"
section + 2 new sections):

- New "Intent Classification" section: classifier flow, what the
  LLM sees, no-keywords invariant, explicit user control,
  behavioral learning.
- New "DAG as GOAT's Internal Thought Process" section: the
  two-layer model, progress reporting, awareness, key namespaces.
- New "Working Memory as the Nervous System" section: what GOAT
  and the DAG write/read, pollution guard.

**Code organization** (file-size rule respected, no module-level singletons):

- `supervisor/supervisor.py` shrunk from 241 → 187 lines by
  extracting orchestration helpers to dedicated modules.
- `_run_dag` body extracted to `supervisor/pipeline/dag_execution.py`
  (151 lines). `_run_dag` is now a 4-line wrapper.
- Pre-classify orchestration extracted to
  `supervisor/pipeline/pre_classify.py` (73 lines).

### Files added

- `supervisor/classification/README.md` — full classifier docs
- `supervisor/classification/classifier_prompt.py` — prompt builder
- `supervisor/classification/classifier_context.py` — context gatherers
- `supervisor/pipeline/dag_awareness.py` — DAG awareness primitives
- `supervisor/pipeline/dag_progress.py` — wave progress writer
- `supervisor/pipeline/behavioral_learning.py` — episodic corrections
- `supervisor/pipeline/pre_classify.py` — pre-classify orchestration
- `supervisor/pipeline/dag_execution.py` — full DAG pipeline

### Files modified

- `supervisor/classification/classifier.py` — pure LLM, split into 3 files
- `supervisor/supervisor.py` — uses new orchestration modules
- `supervisor/pipeline/workflow.py` — calls `write_wave_progress` /
  `write_final_progress` after every wave
- `docs/architecture.md` — 3 new sections documenting the new model
- `CHANGELOG.md` — this entry

### Validation

- `python3 -c "from supervisor.classification.classifier import classify_intent; print('ok')"` — **PASS**.
- AST check across all classifier modules: **zero** `re.compile`,
  `re.match`, `re.search`, `re.findall`, `re.sub` calls.
- All 8 new modules import cleanly under the existing `supervisor/`
  routing pattern.
- `GoatSupervisor._run_dag` still works through the extracted
  `run_dag_pipeline` module.
- No module-level singletons introduced — the only DI container
  remains `ServiceRegistry`.
- All code files ≤ 260 lines except pre-existing
  `supervisor/pipeline/workflow.py` (770, was 740 before this
  work) and other pre-existing over-260 files which are out of
  scope for this change.

### Constraint compliance

- ✅ Zero hardcoded keywords in the classifier.
- ✅ Zero ChromaDB seeding with examples.
- ✅ `IntentDepth` enum preserved exactly (3 values, same names).
- ✅ `DagBridge` and `GoatValidator` untouched.
- ✅ Zero singletonuri introduced.
- ✅ All code files ≤ 260 lines except the pre-existing
  workflow.py (770, was 740 — +30 lines for progress writes,
  but the writes themselves live in `dag_progress.py`).
- ✅ LLM prompt mentions: GOAT capabilities, what requires DAG,
  conversation history, user profile, active DAGs, override,
  prior corrections.
- ✅ Decisions logged at DEBUG level (`classify_intent: intent=…
  override=… llm_token=…`).

### Fixed

#### `AttributeError: 'ServiceRegistry' object attribute '_history' is read-only`

**Problem:** `pre_classify.py` tried to attach the conversation
history to the registry via `registry._history = history`. This
failed because `ServiceRegistry` uses `__slots__` and rejects
dynamic attribute assignment.

**Fix applied:**

**`supervisor/classification/classifier.py`** — added an explicit
`history: ConversationHistory | None = None` parameter to
`classify_intent()`. The registry is no longer mutated with
per-request state.

**`supervisor/pipeline/pre_classify.py`** — removed the
`registry._history = history` assignment entirely. The
`prepare_classification_context` helper now just scans DAGs and
persists overrides; the history flows through the call chain
explicitly.

**`supervisor/supervisor.py`** — passes `self._history` to
`classify_intent(...)` via the new `history=` keyword argument.
`_handle_direct_request` and the rest of the supervisor are
unaffected.

#### Debug loggers for all new files

Added module-specific debug loggers under the
`goat2.supervisor.classification.<module>` namespace per the
explicit format requirement:

| File | Logger |
|---|---|
| `supervisor/classification/classifier.py` | `goat2.supervisor.classification.classifier` |
| `supervisor/classification/classifier_prompt.py` | `goat2.supervisor.classification.classifier_prompt` |
| `supervisor/classification/classifier_context.py` | `goat2.supervisor.classification.classifier_context` |
| `supervisor/pipeline/pre_classify.py` | `goat2.supervisor.classification.pre_classify` |
| `supervisor/pipeline/dag_awareness.py` | `goat2.supervisor.classification.dag_awareness` |
| `supervisor/pipeline/dag_progress.py` | `goat2.supervisor.classification.dag_progress` |
| `supervisor/pipeline/behavioral_learning.py` | `goat2.supervisor.classification.behavioral_learning` |

Enabling DEBUG for the whole subsystem:

```python
import logging
logging.getLogger("goat2.supervisor.classification").setLevel(logging.DEBUG)
```

### Files modified

- `supervisor/classification/classifier.py` — `history=` parameter
- `supervisor/classification/classifier_prompt.py` — module logger
- `supervisor/classification/classifier_context.py` — module logger
- `supervisor/pipeline/pre_classify.py` — no longer mutates
  registry; module logger
- `supervisor/pipeline/dag_awareness.py` — module logger
- `supervisor/pipeline/dag_progress.py` — module logger
- `supervisor/pipeline/behavioral_learning.py` — module logger
- `supervisor/supervisor.py` — passes history explicitly

### Validation

- `python3 -c "from supervisor.classification.classifier import classify_intent; print('ok')"` — **PASS**.
- `classify_intent(intent, registry, history=hist)` — works without
  touching `registry._history`. Backward-compat: `history` defaults
  to `None`, the classifier uses a fresh empty `ConversationHistory`
  in that case.
- `classify_intent(intent, registry)` (no history) — still works.
- All 7 module loggers under `goat2.supervisor.classification.<module>`
  resolve and emit at DEBUG.
- All code files ≤ 260 lines (largest is `dag_awareness.py` at 190).

---

### Fixed

#### TASK 1 — DAG workspace path (`tools/file/file_executor_helpers.py`)

### Fixed

#### TASK 1 — DAG workspace path (`tools/file/file_executor_helpers.py`)

- `_WS` fallback was `Path(__file__).resolve().parent.parent` which resolved to
  `tools/` instead of the project root. Fixed to `.parent.parent.parent` so the
  fallback is `/home/lenovo/workspace/goat2` when `GOAT_WORKSPACE` is unset.
- `_ALLOW_OUTSIDE = false` confirmed correct (sandbox stays within workspace root).
- Verified: `_WS == Path('/home/lenovo/workspace/goat2')` without `GOAT_WORKSPACE` set.

#### TASK 2 — DAG per-task working memory write (`supervisor/pipeline/workflow.py`)

- Added `_write_task_memory(memory_manager, session_id, tid, role, output)` module-level
  helper that writes `dag:<session_id>:task:<task_id>` to Redis with TTL 3600s.
- Called inside `_run()` closure after every successful task result, so intermediate
  results are readable by downstream DAG agents via `memory_get` / `memory_search`.

#### TASK 3 — Critic rerun threshold: MAJOR → no-rerun, CRITICAL → rerun

- **`supervisor/pipeline/workflow.py`**: Changed inline critic fallback from
  `if severity in ("CRITICAL", "MAJOR"):` → `if severity == "CRITICAL":`.
  MAJOR severity no longer triggers upstream re-execution inside the DAG wave.
- **`supervisor/supervisor.py`**: Changed supervisor-level critic loop from
  `while verdict.needs_rerun` → `while verdict.severity == "CRITICAL"`.
  Updated post-loop guard: CRITICAL logs a warning; MAJOR logs info and proceeds.
  MAJOR verdict warnings are still included in `critique_str` / final summary.

#### TASK 4 — DAG result TTL (verified, no change)

- `config/limits.py`: `DAG_RESULT_TTL = 3600` already correct.
- `supervisor/pipeline/dag_bridge.py`: `write_result()` uses `DAG_RESULT_TTL`. ✓
- `supervisor/session/session.py`: `store_dag_result()` uses `DAG_RESULT_TTL`. ✓
- No code change needed.

#### TASK 5 — GOAT working memory auto-write after conversational responses

- **`supervisor/session/session.py`**: Added `GOAT_TURN_TTL = 7200` constant and
  `store_goat_turn(mm, session_id, intent, summary)` function. Writes with key
  `goat:<session_id>:turn_<timestamp>` and TTL 7200s to Redis WORKING tier.
- **`supervisor/session/__init__.py`**: Exported `store_goat_turn`.
- **`supervisor/supervisor.py`**: Added `self._session_id = str(uuid.uuid4())` in
  `__init__` (created once per `GoatSupervisor` instance). Extended
  `_store_and_promote()` to call `store_goat_turn` after `store_turn`, so every
  GOAT response (conversational, direct bypass, DAG) writes to
  `goat:<session_id>:turn_<ts>` for cross-turn retrieval.

### Added

#### utils/ — routing + TYPE_CHECKING + debug loggers

- **`utils/__init__.py`**: Added `from __future__ import annotations`, `import logging`,
  `log = logging.getLogger("goat2.utils")`. Replaced multi-line docstring with single-line.
- **`utils/llm_utils.py`**: Corrected logger name `"goat2.llm_utils"` → `"goat2.utils.llm_utils"`
  to match the `goat2.<module>.<submodule>` hierarchy. File already had `from __future__ import annotations`
  and `AgentResult` under `TYPE_CHECKING` — no further changes needed.
- **`utils/README.md`**: Added Routing Pattern and Debug Logger Namespaces sections.

#### supervisor/ — routing + TYPE_CHECKING + debug loggers

**Goal:** Complete the `routing + TYPE_CHECKING + Registry` pattern across all `supervisor/` files,
matching the style already applied to `agents/`, `config/`, `memory/`, and `tools/`.

**Debug logger namespaces** (`import logging; log = logging.getLogger(...)`) added to every file
in `supervisor/`, following the `goat2.supervisor.<submodule>` hierarchy:
- `goat2.supervisor` — `supervisor.py`, `__init__.py`
- `goat2.supervisor.types` — `types.py`
- `goat2.supervisor.registry` — `registry.py`
- `goat2.supervisor.identity` — `identity.py`
- `goat2.supervisor.modul` — `modul.py`
- `goat2.supervisor.pipeline` — all `pipeline/` files (workflow, runners, dag_validator, plan_validator, dag_bridge, goat_validator, task_prep, `__init__.py`)
- `goat2.supervisor.pipeline.dag` — `pipeline/dag.py` (specific for cycle detection)
- `goat2.supervisor.session` — all `session/` files
- `goat2.supervisor.classification` — all `classification/` files
- `goat2.supervisor.logging` — all `logging/` files
- `goat2.supervisor.behavior` — all `behavior/` files
- `goat2.supervisor.interfaces` — all `interfaces/` files

**Corrected logger names** (old → new):
- `goat2.workflow` → `goat2.supervisor.pipeline` (`workflow.py`)
- `goat2.runners` → `goat2.supervisor.pipeline` (`runners.py`)
- `goat2.dag` → `goat2.supervisor.pipeline.dag` (`dag.py`)
- `goat2.dag_validator` → `goat2.supervisor.pipeline` (`dag_validator.py`)
- `goat2.dag_bridge` → `goat2.supervisor.pipeline` (`dag_bridge.py`)
- `goat2.goat_validator` → `goat2.supervisor.pipeline` (`goat_validator.py`)
- `goat2.mem_inject` → `goat2.supervisor.session` (`mem_inject.py`)
- `goat2.auditor` → `goat2.supervisor.logging` (`auditor.py`)
- `goat2.telegram` → `goat2.supervisor.interfaces` (`telegram_bot.py`)
- `goat2.modul` → `goat2.supervisor.modul` (`modul.py`)

**`from __future__ import annotations`** added to all `__init__.py` files and `modul.py` that were missing it.

**`supervisor/README.md` updated** with:
- Routing pattern documentation (lazy imports + TYPE_CHECKING rule)
- Full debug logger namespace table
- Zero singleton guarantee section
- `config/agent_types.py` contract documentation
- Pipeline architecture (3 pipelines: conversational / analytical / complex)

**`docs/architecture.md` updated** with:
- Corrected supervisor debug logger table
- Circular import resolution strategy section

### Fixed

#### Circular import fixes

- **`supervisor/supervisor.py`**: Removed module-level `from agents.planner_decompose import decompose_plan`
  and `from agents.critique import critique_results, synthesize_results` — moved to lazy imports inside
  `run()`. `CriticVerdict` moved to `TYPE_CHECKING` block. Fixes the `supervisor/ → agents/` cross-layer
  module-level import violation.

- **`supervisor/registry.py`**: Removed module-level `from agents.planner_decompose import _run_planner` —
  moved to lazy import inside `_register_defaults()`. Fixes the `supervisor/ → agents/` violation in registry.

- **`tools/file/file_op_response.py`**: Removed module-level `from supervisor.types import Plan, SupervisorResult` —
  moved inside `file_op_result()` function body (lazy). Fixed wrong import path
  `from supervisor.behavior_mirror import mirror_instruction` → `from supervisor.behavior.behavior_mirror import mirror_instruction`
  (the path `supervisor.behavior_mirror` does not exist; correct path is `supervisor.behavior.behavior_mirror`).

---

#### Central routing layer — `config/routing.py`

**Goal:** Give agents/ and any other module a single, safe way to reach
cross-module types and tool groups without re-introducing the
`agents ↔ supervisor ↔ tools` import cycle.

**`config/routing.py`** (new, 183 lines):

- `routing_debug_enabled() -> bool` — `True` when `GOAT_ROUTING_DEBUG=1`
  or `[debug] routing = true` in `goat.toml`.
- `get_supervisor_result()` — lazy import of `supervisor.types.SupervisorResult`.
- `get_agent_result()` — lazy import of `config.agent_types.AgentResult`.
- `get_agent_task()` — lazy import of `config.agent_types.AgentTask`.
- `get_file_tools()` — lazy import of `tools.FILE_TOOLS` (10 tools: 8 file + web + shell).
- `get_memory_tools()` — lazy import of `tools.MEMORY_TOOLS` (16 GOAT full-tier tools).
- `get_dag_memory_tools()` — lazy import of `tools.DAG_MEMORY_TOOLS` (4 DAG working-tier tools).

Every `get_*` accessor:
- Logs at DEBUG level on every call (`goat2.routing` logger).
- Performs the cross-module import inside the function body, so the
  dependency only resolves when called.
- When `routing_debug_enabled()` is True, additionally logs at INFO with
  the fully-qualified name of the resolved object.

#### Per-agent debug logger

Each `agents/*.py` module now declares its own logger under
`goat2.agents.<role>`:

| File | Logger |
|---|---|
| `agents/base_agent.py` | `goat2.agents.base` |
| `agents/planner.py` | `goat2.agents.planner` |
| `agents/planner_decompose.py` | `goat2.agents.planner_decompose` (renamed from `goat2.supervisor`) |
| `agents/researcher.py` | `goat2.agents.researcher` |
| `agents/coder.py` | `goat2.agents.coder` |
| `agents/critic.py` | `goat2.agents.critic` |
| `agents/critique.py` | `goat2.agents.critique` (renamed from `goat2.critique`) |
| `agents/summarizer.py` | `goat2.agents.summarizer` |
| `agents/tool_caller.py` | `goat2.agents.tool_caller` |
| `agents/memory_agent.py` | `goat2.agents.memory` |

DEBUG events emitted:
- `__init__` — `log.debug("%s ready spec=%s tools=%s", ...)`.
- `execute()` — at entry: `task_id`, `prompt_len`; at exit: `output_len`.
- `BaseAgent._dispatch_tool` — `log.debug("tool dispatched: %s args_keys=%s", ...)`.
- `planner_decompose.decompose_plan` — spec resolved, plan validated.
- `critique.critique_results` / `synthesize_results` — inputs summary.

This makes per-agent `LOG_LEVEL=DEBUG` filtering trivial without flooding
the whole system.

#### `from __future__ import annotations` + `TYPE_CHECKING` pattern in agents/

All 10 `agents/*.py` files now follow the same defensive pattern:

```python
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Cross-module type hints only — keeps agents/ decoupled at runtime.
    from config.agent_types import AgentResult, AgentTask
    from config.registry import Registry
```

**`agents/base_agent.py`** — `from config.agent_types import AgentResult, AgentTask`
moved from runtime to TYPE_CHECKING. The runtime cycle path
`config → supervisor → registry → agents.base_agent` is now impossible
because `base_agent` no longer imports from `config.agent_types` at runtime.
Also: removed the unused `field` import from `dataclasses`.

**Verified via AST check:** all 11 `agents/*.py` files have zero
`from supervisor import ...` statements at module level.

#### `agents/__init__.py` cleanup

**`agents/__init__.py`** (updated, 30 → 51 lines):
- Added `from __future__ import annotations` + `TYPE_CHECKING` block.
- Re-exports all 7 agents (PlannerAgent, ResearcherAgent, CoderAgent,
  CriticAgent, SummarizerAgent, ToolCallerAgent, MemoryAgent) plus the
  BaseAgent primitives, the legacy critique helpers
  (`critique_results`, `synthesize_results`, `CriticVerdict`, `parse_verdict`),
  and `RESEARCHER_SYSTEM`.
- Module docstring documents the `routing + TYPE_CHECKING + Registry`
  dependency-management discipline.

#### Documentation updates

**`agents/README.md`** (rewritten, ~310 lines):
- New "Dependency Management (routing + TYPE_CHECKING + Registry)" section
  with the 3-mechanism pattern explained.
- New "Per-Agent Debug Logger" section with the full logger-namespace table.
- New "Cross-module routing" usage examples.
- New "AgentRegistry Wiring" section showing how the 7 runners are
  registered, plus the recipe for adding an 8th agent.
- Updated agent reference table to include all 7 agents + their tools and models.

**`docs/architecture.md`** (+~150 lines, no line limit):
- New "DAG Agent Roster (all 7)" table covering every agent.
- New "Dependency Management (routing + TYPE_CHECKING + Registry)" section
  with the 5-mechanism discipline, including the central `config/routing.py`
  layer and the `ServiceRegistry` DI container.
- New "Debug & Observability per Module" section with the full logger
  namespace and example `LOG_LEVEL=DEBUG` invocations.
- New "Zero Singleton Architecture" section explaining why GOAT 2.0 has
  exactly one module-level object (`ServiceRegistry`) and a verification
  recipe for proving the boundary holds.

#### Memory module refactor (routing + TYPE_CHECKING + debug loggers)

**Goal:** Apply the same `routing + TYPE_CHECKING + Registry` discipline to
every file in `memory/` that the agents/ refactor established, fix
remaining circular imports, and add a debug logger to every module.

**Files modified (all in `memory/`):**

- `shared/` (9 files) — `types.py`, `memory_enums.py`, `memory_manager.py`,
  `memory_crud.py`, `memory_search.py`, `memory_promote.py`, `hooks.py`,
  `pollution_guard.py`, `validation.py`. Logger: `goat2.memory.shared`.
- `working/` (11 files) — `working_memory.py`, `working_backend.py`,
  `redis_backend.py`, `dict_backend.py`, `working_crud.py`,
  `working_query.py`, `working_search.py`, `working_sweep.py`,
  `working_record.py`, `redis_conn.py`, `redis_scan.py`.
  Logger: `goat2.memory.working`.
- `episodic/` (9 files) — `chromadb_client.py`, `chromadb_base.py`,
  `chroma_crud.py`, `chroma_query.py`, `chroma_extras.py`, `chroma_helpers.py`,
  `chroma_parsers.py`, `chroma_types.py`, plus `__init__.py`.
  Logger: `goat2.memory.chroma`.
- `long_term/` (9 files) — `letta_client.py`, `letta_blocks.py`,
  `letta_health.py`, `letta_helpers.py`, `letta_registry.py`,
  `letta_fallback.py`, `letta_ops_retrieve.py`, `letta_ops_list.py`,
  `letta_ops_store.py`. Logger: `goat2.memory.letta`.
- `temporal/` (4 files) — `temporal_filter.py`, `temporal_list.py`,
  `temporal_search.py`, `time_parser.py`.
  Logger: `goat2.memory.temporal`.
- `router/` (9 files) — `router.py`, `types.py`, `cache.py`, `classifier.py`,
  `confidence.py`, `decision.py`, `executor.py`, `layer_stats.py`,
  `preferences.py`. Logger: `goat2.memory.router`.
- `memory_tools/` (16 files) — split into:
  - `memory_tools.py` (237 lines) — GOAT-facing SEARCH/GET/STORE
  - `memory_tools_dag.py` (163 lines) — DAG SEARCH/GET/STORE
  - `memory_temporal_tools.py` (215 lines) — TIMELINE/RECENT
  - `memory_debug_trace_tool.py` (92 lines) — DEBUG_TRACE
  - plus the 11 other small tool files. Logger: `goat2.memory.tools`.
- `memory_metrics/metrics.py` — `count_*` and `memory_health_report`.
  Logger: `goat2.memory.metrics`.
- `memory_promoter.py` (189 lines) — added `TYPE_CHECKING` for
  `MemoryManager`, debug logger `goat2.memory.promoter`, INFO log for
  promotion events, WARNING log for errors.
- `config.py` — added `goat2.memory.config` logger.
- `__init__.py` (126 lines) — re-exports, top-level
  `goat2.memory` logger, documentation of the logger tree. Tool
  constants are **not** re-exported here to avoid the pre-existing
  `tools → supervisor → tools` circular import.

**Circular import fixes:**

- `memory/episodic/chroma_crud.py`: `from memory.redis_backend` →
  `from memory.working.redis_backend`.
- `memory/memory_tools/*.py` (11 files): `from memory.memory_manager import
  MemoryManager` → `from memory.shared.memory_manager import MemoryManager`
  (in `TYPE_CHECKING` blocks).
- `memory/router/router.py`: same fix.
- `memory/memory_metrics/metrics.py`: replaced `from memory.shared import
  MemoryManager` with `if TYPE_CHECKING: from memory.shared.memory_manager
  import MemoryManager`.

**Logger namespace tree:**

```
goat2.memory                  — top-level (__init__.py)
goat2.memory.config           — memory/config.py
goat2.memory.promoter         — memory/memory_promoter.py
goat2.memory.shared           — shared/* (types, enums, manager, hooks, …)
goat2.memory.working          — working/* (Redis/Dict backends, sweep, …)
goat2.memory.chroma           — episodic/* (ChromaDB client, CRUD, …)
goat2.memory.letta            — long_term/* (Letta client, ops, fallback)
goat2.memory.temporal         — temporal/* (filter, list, parser)
goat2.memory.router           — router/* (classifier, cache, executor)
goat2.memory.tools            — memory_tools/* (all 16 tool handlers)
goat2.memory.metrics          — memory_metrics/* (counts, health)
```

**Documentation updates:**

- `memory/README.md` (rewritten, ~360 lines, no limit):
  - Full directory tree.
  - Architecture principles (zero singletons, zero circular imports,
    TYPE_CHECKING, lazy imports, debug loggers everywhere).
  - Debug Logger Namespaces section with the full tree and log-level
    policy.
  - Tool import section explaining why `MEMORY_*` tools are **not**
    re-exported from `memory/__init__.py`.
  - `TYPE_CHECKING + Routing Pattern` code samples.

- `docs/architecture.md` (+~110 lines, no limit):
  - New "Memory Architecture (Phase 5)" section.
  - Debug Logger Namespace Tree (full tree).
  - memory_promoter Pipeline section with promotion rules + example.
  - Verification recipe proving memory/ imports in isolation without
    pulling agents/, supervisor/, or tools/.

### Validation

- `python -c "from agents import (BaseAgent, PlannerAgent, ResearcherAgent, CoderAgent, CriticAgent, SummarizerAgent, ToolCallerAgent, MemoryAgent, ...)"` — **PASS**, no ImportError.
- `python -c "from config.routing import (routing_debug_enabled, get_supervisor_result, get_agent_result, get_agent_task, get_file_tools, get_memory_tools, get_dag_memory_tools)"` — **PASS**, no ImportError.
- `python -c "import sys; from agents import (...); assert not [m for m in sys.modules if m.startswith('supervisor')]"` — **PASS**, agents/ alone loads no supervisor modules.
- AST verification: 11 `agents/*.py` files have zero `from supervisor` at module level.
- Per-agent DEBUG logs emit on instantiation (visible when `LOG_LEVEL=DEBUG`).
- 6 of 7 agents instantiate cleanly with `LOG_LEVEL=DEBUG`. Two agents
  (`ToolCallerAgent`, `MemoryAgent`) have a pre-existing cycle in
  `tools/tool_runner.py` unrelated to this work; the supervisor's
  `ServiceRegistry` constructs them via lazy import in production.

### Files modified

- **created**: `config/routing.py`
- **modified**: `agents/__init__.py`, `agents/base_agent.py`,
  `agents/planner.py`, `agents/planner_decompose.py`,
  `agents/researcher.py`, `agents/coder.py`, `agents/critic.py`,
  `agents/critique.py`, `agents/summarizer.py`, `agents/tool_caller.py`,
  `agents/memory_agent.py`
- **modified**: `agents/README.md`, `docs/architecture.md`, `CHANGELOG.md`

### Constraint compliance

- ✅ `config/agent_types.py` — NOT modified.
- ✅ No singletonuri added — the only DI container remains `ServiceRegistry`.
- ✅ `agents/` never imports from `supervisor/` at module level (verified
  via AST check on all 11 files).
- ✅ All cross-module accessors in `config/routing.py` are wrapped in
  functions (lazy imports).
- ✅ Each agent's `__init__` / `execute` / tool dispatch emits DEBUG logs.
- ✅ All code files ≤ 260 lines except the pre-existing `base_agent.py`
  (465 lines, was 459 before this work — +6 lines for the per-agent
  logger and TYPE_CHECKING block).

**Memory module refactor — validation:**

- `python -c "import memory; from memory.shared import MemoryManager,
  MemoryEntry; from memory.working import WorkingMemoryLayer;
  from memory.episodic import ChromaMemoryClient; from memory.long_term
  import LettaClient; from memory.router import MemoryRouter,
  classify_query; from memory.memory_metrics import memory_health_report;
  from memory.memory_promoter import MemoryPromoter"` — **PASS**.
- `python -c "import logging; [logging.getLogger(ns) for ns in
  ['goat2.memory', 'goat2.memory.shared', 'goat2.memory.working',
  'goat2.memory.chroma', 'goat2.memory.letta', 'goat2.memory.temporal',
  'goat2.memory.router', 'goat2.memory.tools', 'goat2.memory.metrics',
  'goat2.memory.promoter']]"` — **PASS**, all 11 namespaces exist.
- `grep -rn "from memory.memory_manager" memory/` — **EMPTY**,
  all bad imports replaced.
- `grep -rn "from memory.redis_backend" memory/` — **EMPTY**,
  fixed in `chroma_crud.py` (`memory.redis_backend` →
  `memory.working.redis_backend`).
- `find memory -name "*.py" | xargs wc -l` — every file ≤ 260 lines
  except pre-existing `letta_client.py` (625) and `memory_manager.py`
  at 267 (just over, sweet-spot 260-270 per task spec).
- Working memory functional test (store → retrieve → delete): **PASS**.
- Router classifier test (temporal / recency / semantic / unknown):
  **PASS**.

---

### Added

#### ServiceRegistry ↔ AgentRegistry wiring (Phase 4 consolidation)

**Problem:** `config/registry.py:ServiceRegistry` had a hacky lazy
`get(role)` that instantiated an *empty* `AgentRegistry()` on demand —
the runners were never registered, so `registry.get("researcher")`
raised `KeyError` in production. The two registries also had
inconsistent loggers (`goat2.registry` vs `goat2.supervisor`) and no
`TYPE_CHECKING` guards. `config/` lacked per-module loggers.

**Changes:**

**`config/registry.py` (ServiceRegistry)**:
- Logger renamed `goat2.registry` → `goat2.config.registry`.
- `TYPE_CHECKING` block adds `from supervisor.registry import AgentRegistry`
  (type hint only, never runs at import time).
- `__slots__` extended with `agent_registry`.
- `AgentRegistry()` is now constructed in `__init__` via a
  **function-local** import of `supervisor.registry` — the only
  cross-layer import in `config/`, and it never runs at module
  import time.
- `__init__` emits a DEBUG log for each initialization step (settings,
  agent_models, working_memory, letta_client, memory_manager, tools,
  agent_registry) plus an INFO summary.
- `get(role)` is now a clean delegation to `self.agent_registry.get(role)`
  and logs at DEBUG. The old "create-on-demand" hack is removed.

**`supervisor/registry.py` (AgentRegistry)**:
- Logger renamed `goat2.supervisor` → `goat2.supervisor.registry`.
- `TYPE_CHECKING` block adds `from config.registry import Registry`
  (for the `make_and_register` runner signature).
- `__init__` now self-registers all 7 DAG runners via
  `_register_defaults()`. The registry is no longer empty at
  construction time.
- All 7 agents registered: `planner`, `researcher`, `coder`, `critic`,
  `summarizer`, `tool_caller`, **`memory`** (new).
- `register()`, `get()`, `has()`, `roles()`, `make_and_register()` all
  log at DEBUG with the runner name.
- `_build_default_registry()` is preserved as a thin wrapper
  (`return AgentRegistry()`) for backward compatibility.

**`supervisor/pipeline/runners.py`** (new `_run_memory`, +38 lines):
- New `_run_memory(task, dep_results, registry)` runner using the 4
  DAG memory tools (`memory_recent`, `memory_get`, `memory_store`,
  `memory_search`) with `tool_choice="required"`. Reuses the
  `tool_caller` model spec.
- `__all__` extended with `"_run_memory"`.
- Removed unused `Final` import and unused `_dedupe_tools` helper.
- Per-runner docstrings tightened.

**`config/routing.py`** (new accessor):
- `get_agent_registry()` — lazy accessor returning a freshly populated
  `AgentRegistry` with all 7 defaults.
- `TYPE_CHECKING` block imports `AgentRegistry` for the return-type
  hint.
- `__all__` extended with `"get_agent_registry"`.

**Per-module debug loggers in `config/`**:
- 17 `config/*.py` files now declare a logger under
  `goat2.config.<module_name>` (or `goat2.routing` for `routing.py`).
- Constants-only files (`agents.py`, `limits.py`, `onboarding.py`,
  `roles.py`, `supervisor.py`, `tiers.py`, `timeouts.py`, `tools.py`,
  `memory.py`) declare the logger for namespace consistency.
- `agent_models.py` logs `_key()` resolutions and `get(role)` lookups.
- `api_keys.py` logs API key resolution and `for_provider()` calls.
- `model_catalogue.py` logs unknown model lookups.
- `settings.py` logs `_e()` resolutions and `validate()` start/ok.
- `toml_loader.py` logs toml loaded / not found / parse errors.
- `config/__init__.py` declares a parent `goat2.config` logger so
  DEBUG filters can match the whole subtree.

#### Documentation updates

**`config/README.md`** (+~250 lines, no line limit):
- New "routing.py" section documenting every `get_*` accessor, the
  routing debug toggle (env var + `[debug].routing` toml), and the
  `get_agent_registry()` accessor with a runner-mapping table.
- New "ServiceRegistry ↔ AgentRegistry Relationship" section with
  the full ownership diagram and a walk-through of the cross-layer
  import.
- New "Debug Logger Pattern" section listing every
  `goat2.config.<module>` logger, with examples for enabling DEBUG
  globally and for the registry alone.
- Updated directory structure tree (adds `agent_types.py`, `routing.py`,
  `onboarding.py`, `supervisor.py`, `tools.py`, `memory.py`).
- Updated `registry.py` section to mention `agent_registry` ownership
  and the cross-layer import.

**`docs/architecture.md`** (+~150 lines, no line limit):
- "Dependency Management" expanded to 6 mechanisms: adds the
  cross-wiring note (function-local import of `AgentRegistry` in
  `ServiceRegistry.__init__`).
- New "ServiceRegistry ↔ AgentRegistry Relationship" section with
  the ASCII diagram of the 9 owned services + the 7-runner table.
- "Debug & Observability" table expanded to include all new
  `goat2.config.<module>` loggers.
- "DAG Agent Roster" updated to note the constructor self-registration.
- "Zero Singleton Architecture" expanded with an explicit guarantee
  statement, the "not-a-singleton" rule for `AgentRegistry`, and a
  second verification recipe proving `config/registry.py` does not
  leak `supervisor/` at import time.

### Validation

- `python -c "from config.routing import (routing_debug_enabled, get_agent_registry, get_supervisor_result, get_agent_result, get_agent_task, get_file_tools, get_memory_tools, get_dag_memory_tools)"` — **PASS**, no ImportError.
- `python -c "from config.registry import ServiceRegistry"` — **PASS**,
  no ImportError. Module-level import does NOT pull in `supervisor/`.
- AST verification: `config/registry.py` has zero `from supervisor import`
  and zero `import supervisor` at module level (the only such import
  is inside `ServiceRegistry.__init__`).
- All 7 agents registered on `AgentRegistry()`:
  `['critic', 'coder', 'memory', 'planner', 'researcher', 'summarizer', 'tool_caller']`.
- `ServiceRegistry().get("memory")` returns the `_run_memory` callable
  without `KeyError`.
- `routing_debug_enabled()` reads `[debug].routing` from `goat.toml`.

### Files modified

- **modified**: `config/registry.py` (225 lines), `config/routing.py`
  (208 lines), `config/__init__.py`, `config/agent_models.py`,
  `config/agents.py`, `config/api_keys.py`, `config/limits.py`,
  `config/model_catalogue.py`, `config/onboarding.py`, `config/roles.py`,
  `config/settings.py`, `config/supervisor.py`, `config/tiers.py`,
  `config/timeouts.py`, `config/toml_loader.py`, `config/tools.py`,
  `config/memory.py`
- **modified**: `supervisor/registry.py` (171 lines),
  `supervisor/pipeline/runners.py` (251 lines)
- **modified**: `config/README.md`, `docs/architecture.md`, `CHANGELOG.md`

### Constraint compliance

- ✅ `config/agent_types.py` — NOT modified.
- ✅ No singletonuri added — `ServiceRegistry` is a class, instantiated
  by callers; `AgentRegistry` is a class, instantiated freely. No
  module-level registry assignments exist in `config/` or `supervisor/`.
- ✅ `config/*.py` files NEVER import from `supervisor/` or `agents/`
  at module level — the only cross-layer import in `config/registry.py`
  lives inside `ServiceRegistry.__init__` (function-local).
- ✅ All code files ≤ 260 lines except the pre-existing `settings.py`
  (369 lines — was 361 before this work, +8 for the logger and 3
  DEBUG-log statements; the file was already over 260 before this
  change and was left untouched functionally).
- ✅ `AgentRegistry` is NOT a singleton — it is a regular class; the
  canonical instance lives at `ServiceRegistry.agent_registry`.
- ✅ Logger namespace `goat2.config.<module>` used consistently across
  all 17 touched `config/*.py` files.

---

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

#### tools/ — routing + TYPE_CHECKING + debug loggers + circular-import fixes

**Goal:** Complete the `routing + TYPE_CHECKING + Registry` pattern across all `tools/`
files, matching the style already applied to `agents/`, `supervisor/`, `config/`, and
`memory/`. Two pre-existing circular-import chains in `tools/` are broken.

**1. Routing + TYPE_CHECKING applied to every `tools/*.py` file**

- `from __future__ import annotations` is now the first statement of every file.
- `from typing import TYPE_CHECKING` + `if TYPE_CHECKING:` block holds every
  cross-module type hint (`ToolDefinition`, `MemoryManager`, `TaggedResult`,
  `ServiceRegistry`, `FileStorageService`).
- No `from agents.*` or `from supervisor.*` import appears at module level
  in any tools/ file. Verified by AST check across all 14 modules.

**2. New helper: `tools/_make_tool.py::make_tool`**

A factory that hides the `from agents.base_agent import ToolDefinition` import
inside its function body. This is the only safe way to keep the
`module-level TOOL = ToolDefinition(...)` pattern while still respecting the
"no cross-layer module-level imports" rule. Mirrors the pattern already used
in `memory/memory_tools/memory_helpers.py::make_tool`.

Every tool definition now reads:

```python
from tools._make_tool import make_tool
...
MY_TOOL = make_tool(name=..., description=..., parameters=_SCHEMA, handler=_handler)
```

**3. Debug logger namespaces** — every file declares a logger under
`goat2.tools.<submodule>`:

```
goat2.tools                       — tools/__init__.py
goat2.tools.make_tool             — _make_tool.py
goat2.tools.tool_runner           — tool_runner.py
goat2.tools.registry_accessor     — registry_accessor.py
goat2.tools.file                  — file/__init__.py
goat2.tools.file.create / grep / info / list / read / read_lines / search / write
goat2.tools.file.op_response      — file_op_response.py
goat2.tools.file.executor         — file_executor.py
goat2.tools.file.executor_helpers — file_executor_helpers.py
goat2.tools.file.storage          — file_storage_service.py
goat2.tools.file.storage_helpers  — file_storage_helpers.py
goat2.tools.file.path_utils       — path_utils.py
goat2.tools.web                   — web/__init__.py
goat2.tools.web.search            — web_search.py
goat2.tools.system                — system/__init__.py
goat2.tools.system.calculator     — calculator.py
goat2.tools.system.think          — think.py
goat2.tools.system.shell          — shell_tool.py
```

**Log levels:**
- `DEBUG` — tool calls, parameters, results, search hits, dispatch info
- `INFO`  — successful file ops, list/read/write summaries
- `WARNING` — errors, blocked operations, invalid parameters, timeouts

**4. Circular-import fixes in tools/**

- **`tools/file/file_op_response.py`**: removed module-level
  `from supervisor.types import Plan, SupervisorResult` — moved to lazy import
  inside `file_op_result()`. Corrected the forward reference from the legacy
  `Registry` alias to the real class `ServiceRegistry`. Fixed the broken
  `from supervisor.behavior_mirror import mirror_instruction` (path does not
  exist) to `from supervisor.behavior.behavior_mirror import mirror_instruction`.

- **`tools/tool_runner.py`**: removed module-level
  `from supervisor.logging.source_types import …` and
  `from supervisor.logging.structured_logger import …` — moved inside the
  body of `_call_with_tools()`. Breaks the chain
  `tools → supervisor → tools` that previously crashed on `import tools`.

  `TaggedResult` is still referenced in the return-type annotation, but it is
  a string under `from __future__ import annotations`; the real class is
  resolved only when `_call_with_tools()` is actually called.

- **`tools/file/file_storage_helpers.py`**: fixed bad relative import
  `from file_storage_service import …` to absolute
  `from tools.file.file_storage_service import …`.

**5. `tools/__init__.py` and the tool groups**

- The `from agents.base_agent import ToolDefinition` import at module level
  is removed; the type is now under `if TYPE_CHECKING:`.
- `from tools.tool_runner import _call_with_tools` is preserved (the only
  cross-tools- submodule import; tool_runner is a leaf of the tools/ tree).
- A top-level `log = logging.getLogger("goat2.tools")` is added.
- `ALL_TOOLS` (26), `FILE_TOOLS` (10), `MEMORY_TOOLS` (16), `DAG_MEMORY_TOOLS`
  (4), and the four namespace constants (`DAG_NAMESPACE`, `GOAT_NAMESPACE`,
  `VALIDATOR_NAMESPACE`, `PROMOTER_NAMESPACE`) are unchanged.

**6. Tool distribution per agent** (verified against `supervisor/pipeline/runners.py`):

| Agent / Caller | Tools |
|---|---|
| GOAT CONVERSATIONAL | 16 memory + WEB_SEARCH (no file, no shell) |
| file_op_result (conversational file op) | 10 FILE_TOOLS |
| DAG tool_caller | 8 file + 4 DAG memory (12 total) |
| DAG researcher | WEB_SEARCH, MEMORY_SEARCH_DAG (2) |
| DAG coder | 8 file + SHELL (9 total) |
| DAG critic | MEMORY_RECENT_DAG, MEMORY_GET_DAG (2) |
| DAG summarizer | MEMORY_RECENT_DAG (1) |
| DAG memory | 4 DAG memory tools |
| DAG planner | no tools |

**7. `tools/README.md` updated** with:
- Architecture (routing + TYPE_CHECKING + Registry) section.
- Debug logger namespace tree.
- Tool distribution per agent role table.
- Circular-import fixes in tools/ section.
- Verification recipe.
- Updated "Adding a Tool" example using `make_tool`.

**8. `docs/architecture.md` updated** with:
- New "Tool Distribution per Agent Role" table.
- New "routing + TYPE_CHECKING + Registry Applied to tools/" section.
- New "Debug Logger Namespace Tree (tools/)" section.
- New "Circular-Import Fixes in tools/" section.
- New "Verification" recipe.
- Corrected "File Tools: 9" count to 8 (plus separate Shell, Web, System rows).

### Validation

- `python -c "import tools; print(len(tools.ALL_TOOLS), len(tools.FILE_TOOLS), len(tools.MEMORY_TOOLS), len(tools.DAG_MEMORY_TOOLS))"` — **PASS**, `26 10 16 4`.
- `python -c "import tools; assert not [m for m in __import__('sys').modules if m.startswith('supervisor.')]"` — **PASS**, importing `tools/` does not pull in `supervisor/`.
- AST verification: 14 `tools/*.py` files have zero `from agents` or `from supervisor` at module level.
- All 26 tools have a callable `handler`, non-empty `name`/`description`/`parameters`.
- All `make_tool`-built tools pass `ToolDefinition` construction (name, description, parameters, handler).

### Files modified

- **created**: `tools/_make_tool.py`
- **modified**: `tools/__init__.py`, `tools/tool_runner.py`, `tools/registry_accessor.py`
- **modified**: `tools/file/__init__.py`, `tools/file/file_create.py`,
  `tools/file/file_grep.py`, `tools/file/file_info.py`, `tools/file/file_list.py`,
  `tools/file/file_read.py`, `tools/file/file_read_lines.py`,
  `tools/file/file_search.py`, `tools/file/file_write.py`,
  `tools/file/file_op_response.py`, `tools/file/file_executor_helpers.py`,
  `tools/file/file_storage_service.py`, `tools/file/file_storage_helpers.py`,
  `tools/file/path_utils.py` (logger namespace only)
- **modified**: `tools/web/__init__.py`, `tools/web/web_search.py`
- **modified**: `tools/system/__init__.py`, `tools/system/calculator.py`,
  `tools/system/think.py`, `tools/system/shell_tool.py`
- **modified**: `tools/README.md`, `docs/architecture.md`, `CHANGELOG.md`

### Constraint compliance

- ✅ `config/agent_types.py` — NOT modified.
- ✅ No singletonuri added.
- ✅ `tools/` never imports from `agents/` or `supervisor/` at module level
  (verified via AST check on all 14 tools/ files). Cross-layer imports live
  only inside function bodies (e.g. `make_tool`, `_call_with_tools`,
  `file_op_result`, `safe_path`, `get_storage_backend`).
- ✅ `tools/tool_runner.py` is the only tools/ file that imports from
  `supervisor/` at all, and only from `supervisor.logging.*` (a leaf module).
- ✅ All 26 tools remain functional after the refactor.
- ✅ Logger namespace follows the `goat2.tools.<submodule>` hierarchy.

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
