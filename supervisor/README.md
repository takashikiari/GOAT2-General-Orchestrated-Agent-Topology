# supervisor/ — GOAT 2.0 Workflow Orchestration

Multi-agent supervisor system with DAG execution, behavioral learning, and tiered memory access.

## Quick Start

```python
from supervisor import GoatSupervisor
from config.registry import ServiceRegistry

registry = ServiceRegistry()
sv = GoatSupervisor(registry)
result = await sv.run("Build a REST API for a todo app")
```

## Directory Structure

```
supervisor/
├── behavior/          # Behavioral learning: style analysis, mirroring, persistence
│   ├── behavior_analyzer.py   — Infer user communication style from turns
│   ├── behavior_mirror.py     — Format style profile for system prompt
│   ├── behavior_profile.py     — BehaviorProfile TypedDict + serialization
│   ├── behavior_session.py    — Session-end style lifecycle
│   ├── behavior_store.py      — Letta 'persona' block persistence
│   ├── info_extract.py       — Fact extraction from user messages
│   └── info_types.py       — Fact confidence types
│
├── pipeline/          # DAG execution: workflow, runners, validation
│   ├── workflow.py        — WorkflowGraph with wave-level concurrency
│   ├── dag.py           — DAGraph, DAGNode, DAGEdge primitives
│   ├── dag_validator.py  — Post-execution result validation
│   ├── plan_validator.py — Pre-execution plan validation
│   ├── runners.py       — Agent runners (researcher, coder, critic, etc.)
│   └── task_prep.py    — Task preparation (memory_manager, language injection)
│
├── session/          # Session management: turns, history, memory injection
│   ├── session.py       — Turn/DAG result storage to Redis
│   ├── session_init.py  — Concurrent session startup
│   ├── history.py      — ConversationHistory, session summary
│   └── mem_inject.py   — Cross-tier memory recall
│
├── classification/   # Intent classification: depth routing, language detection
│   ├── classifier.py          — IntentDepth (conversational/analytical/complex)
│   ├── request_classifier.py   — Direct request bypass detection
│   └── lang_detect.py       — Language detection
│
├── logging/         # Structured logging: audit, provenance, tool call tracing
│   ├── auditor.py          — Cross-tool consistency check
│   ├── structured_logger.py — JSON tool call logging
│   └── source_types.py     — SourceTag, TaggedResult types
│
├── interfaces/      # External interfaces (keep as-is)
│   ├── content_filter.py
│   └── telegram_bot.py
│
├── supervisor.py   # GoatSupervisor — main orchestrator
├── identity.py    # GOAT_SYSTEM, profile loading, direct_response
├── types.py       # AgentTask, AgentResult, Plan, SupervisorResult
├── registry.py     # AgentRegistry (not a singleton)
├── planner.py    # Task decomposition (DEPRECATED: use agents.planner_decompose)
├── critique.py    # Critique and synthesis (DEPRECATED: use agents.critique)
├── llm_utils.py   # LLM client utilities (DEPRECATED: use utils.llm_utils)
├── tool_runner.py # Tool-calling loop (DEPRECATED: use tools.tool_runner)
├── file_op_response.py # File operation handler (DEPRECATED: use tools.file.file_op_response)
└── __init__.py   # Re-exports for backward compatibility
```

**Note**: The following modules have been moved to more appropriate locations:
- `planner.py` → `agents.planner_decompose.py`
- `critique.py` → `agents/critique.py`
- `llm_utils.py` → `utils/llm_utils.py`
- `tool_runner.py` → `tools/tool_runner.py`
- `file_op_response.py` → `tools/file/file_op_response.py`

The old locations in `supervisor/` still work via backward compatibility shims.
```

## How GoatSupervisor Orchestrates the 3 Async Pipelines

### Pipeline 1: Conversational Turn (no DAG)
```
intent → classify_intent → CONVERSATIONAL → direct_response
```
- No DAG execution
- Direct LLM call with CORE_TOOLS (FILE_TOOLS + MEMORY_TOOLS)
- Temperature: 0.7 for natural conversation

### Pipeline 2: Analytical (lightweight DAG)
```
intent → classify_intent → ANALYTICAL → decompose_plan(lightweight)
    → WorkflowGraph (≤2 tasks) → synthesize
```
- Lightweight task decomposition (max 2 tasks)
- Wave-level concurrency with semaphore limit
- Synthesis from task outputs

### Pipeline 3: Complex (full DAG)
```
intent → classify_intent → COMPLEX → decompose_plan
    → WorkflowGraph → validate_results → critique → synthesize
    → run_auditor (cross-tool consistency)
```
- Full task decomposition with dependencies
- Wave execution via Kahn's algorithm
- Critical review fallback (re-execute on CRITICAL/MAJOR)
- Final synthesis + audit

## DAG Execution Flow

```
decompose_plan(intent)
    ↓
Plan.validate() — structural validation
    ↓
WorkflowGraph.execute(plan.tasks, registry)
    ├── Wave 1: tasks with no dependencies (parallel)
    ├── Wave 2: tasks depending on Wave 1
    └── ... continue until all done
    ↓
validate_results() — post-execution validation
    ↓
critique_results() — verify task success
    ↓
synthesize_results() — produce final answer
    ↓
run_auditor() — cross-tool consistency check
```

### Wave-Level Concurrency

- Tasks grouped into waves by topological sort (Kahn's algorithm)
- Each wave executes in parallel (bounded by semaphore)
- Next wave starts after all tasks in current wave complete
- Maximum 10 waves, 5 tasks per wave (from config/supervisor.py)

## Behavioral Learning Integration

### Session Start
```
init_session(mm)
    → load_user_profile(mm)      # Letta 'human' block
    → load_session_summary(mm)   # ChromaDB
    → load_style(mm)           # Letta 'persona' block
    → check_onboarding_done(mm) # Redis flag
```

### Every Response
```
_system_with_profile(profile, summary, style)
    → GOAT_SYSTEM
    → mirror_instruction(style)  # "formality: casual; tone: technical..."
    → profile (filtered)
    → summary (previous sessions)
```

### Session End
```
finalize_behavior(mm, history, style)
    → analyze_style(user_turns)
    → save_style(style)  # Letta 'goat/persona'
```

## Classification and Routing

### IntentDepth Levels

| Level | Handler | Description |
|-------|---------|-------------|
| CONVERSATIONAL | direct_response | Questions, greetings, simple Q&A |
| ANALYTICAL | lightweight DAG | Comparisons, light coding, analysis |
| COMPLEX | full DAG | Implementation, research, design |

### Help Detection (Onboarding)
- Patterns: help, ?, capabilities, commands, "ce poți face"
- Forces CONVERSATIONAL mode for onboarding queries

### First Message Guard
- Vague patterns: greetings, empty, punctuation only
- Forces CONVERSATIONAL for first interaction

## Import Examples

```python
# Core classes (backward compatible)
from supervisor import (
    GoatSupervisor,
    WorkflowGraph,
    AgentRegistry,
    AgentTask,
    AgentResult,
    Plan,
    SupervisorResult,
)

# Behavior module
from supervisor.behavior import (
    analyze_style,
    mirror_instruction,
    BehaviorProfile,
    finalize_behavior,
    load_style,
    save_style,
    maybe_store_info,
)

# Pipeline module
from supervisor.pipeline import (
    WorkflowGraph,
    validate_plan,
    validate_results,
    prepare_tasks,
    _run_researcher,
    _run_coder,
    _run_critic,
)

# Session module
from supervisor.session import (
    store_turn,
    store_dag_result,
    retrieve_dag_result,
    ConversationHistory,
    init_session,
    mem_turn,
)

# Classification module
from supervisor.classification import (
    IntentDepth,
    classify_intent,
    classify_direct_request,
    detect_language,
)

# Logging module
from supervisor.logging import (
    AuditReport,
    run_auditor,
    log_tool_call,
    SourceTag,
    TaggedResult,
)
```

## Memory Access Architecture

### Supervisor (Full Access)
- WORKING (Redis): Session-scoped with TTL
- EPISODIC (ChromaDB): Semantic search
- LONG_TERM (Letta): Core memory blocks

### DAG Agents (Restricted Access)
- Only WORKING memory via task.memory_manager
- Cannot access ChromaDB or Letta
- Prevents memory pollution

## Configuration

Constants in `config/supervisor.py`:
- MAX_WAVES: 10 — maximum concurrent waves
- MAX_TASKS_PER_WAVE: 5 — tasks per wave
- SYNTHESIS_TEMPERATURE: 0.3 — critique/synthesis temp
- DEFAULT_TIMEOUT_SECONDS: 300 — task timeout

---

## Dependency Management (routing + TYPE_CHECKING + Registry)

This follows the same pattern as `agents/` — see `agents/README.md` for the full rationale.

### Rule: supervisor/ may import from agents/ and tools/ ONLY via lazy imports or TYPE_CHECKING

Every cross-layer import from `agents/` or `tools/` in `supervisor/` must be:

1. **Lazy (function-local)** when the value is used at runtime:
   ```python
   async def run(self, intent: str) -> SupervisorResult:
       from agents.planner_decompose import decompose_plan  # lazy: agents/ cross-layer
       plan = await decompose_plan(plan_ctx, self.registry)
   ```

2. **TYPE_CHECKING guard** when used only as a type hint:
   ```python
   if TYPE_CHECKING:
       from agents.critique import CriticVerdict
   ```

Files that follow this pattern:
- `supervisor/supervisor.py` — agents imports lazy inside `run()`, CriticVerdict under TYPE_CHECKING
- `supervisor/registry.py` — `_run_planner` lazy inside `_register_defaults()`
- `tools/file/file_op_response.py` — supervisor.types lazy inside `file_op_result()`

### Zero Singleton Guarantee

`AgentRegistry` is **not** a singleton — construct as many instances as needed.
The canonical instance lives at `ServiceRegistry.agent_registry` (dependency injection).
No module-level instances exist in supervisor/ except in application entry points
(`interfaces/telegram_bot.py` initialises `_registry` at startup — this is intentional).

### config/agent_types.py Contract

`config/agent_types.py` contains **pure dataclasses only**: `AgentRunner`, `TaskStatus`,
`AgentTask`, `AgentResult`, `Plan`. No logic, no methods that call external services,
no side effects. Any method that does something useful goes in `supervisor/types.py` or a
separate validator module. This separation allows `agents/` and `tools/` to import these
types without triggering the supervisor import chain.

---

## Debug Logger Namespace Tree

Every supervisor/ file declares a logger under `goat2.supervisor.*`:

| Module | Logger name |
|---|---|
| `supervisor/__init__.py` | `goat2.supervisor` |
| `supervisor/supervisor.py` | `goat2.supervisor` |
| `supervisor/types.py` | `goat2.supervisor.types` |
| `supervisor/registry.py` | `goat2.supervisor.registry` |
| `supervisor/identity.py` | `goat2.supervisor.identity` |
| `supervisor/modul.py` | `goat2.supervisor.modul` |
| `supervisor/pipeline/__init__.py` | `goat2.supervisor.pipeline` |
| `supervisor/pipeline/workflow.py` | `goat2.supervisor.pipeline` |
| `supervisor/pipeline/runners.py` | `goat2.supervisor.pipeline` |
| `supervisor/pipeline/dag.py` | `goat2.supervisor.pipeline.dag` |
| `supervisor/pipeline/dag_validator.py` | `goat2.supervisor.pipeline` |
| `supervisor/pipeline/plan_validator.py` | `goat2.supervisor.pipeline` |
| `supervisor/pipeline/dag_bridge.py` | `goat2.supervisor.pipeline` |
| `supervisor/pipeline/goat_validator.py` | `goat2.supervisor.pipeline` |
| `supervisor/pipeline/task_prep.py` | `goat2.supervisor.pipeline` |
| `supervisor/session/__init__.py` | `goat2.supervisor.session` |
| `supervisor/session/session.py` | `goat2.supervisor.session` |
| `supervisor/session/history.py` | `goat2.supervisor.session` |
| `supervisor/session/mem_inject.py` | `goat2.supervisor.session` |
| `supervisor/session/session_init.py` | `goat2.supervisor.session` |
| `supervisor/classification/__init__.py` | `goat2.supervisor.classification` |
| `supervisor/classification/classifier.py` | `goat2.supervisor.classification` |
| `supervisor/classification/lang_detect.py` | `goat2.supervisor.classification` |
| `supervisor/classification/request_classifier.py` | `goat2.supervisor.classification` |
| `supervisor/logging/__init__.py` | `goat2.supervisor.logging` |
| `supervisor/logging/auditor.py` | `goat2.supervisor.logging` |
| `supervisor/logging/structured_logger.py` | `goat2.supervisor.logging` |
| `supervisor/logging/source_types.py` | `goat2.supervisor.logging` |
| `supervisor/behavior/__init__.py` | `goat2.supervisor.behavior` |
| `supervisor/behavior/behavior_analyzer.py` | `goat2.supervisor.behavior` |
| `supervisor/behavior/behavior_mirror.py` | `goat2.supervisor.behavior` |
| `supervisor/behavior/behavior_profile.py` | `goat2.supervisor.behavior` |
| `supervisor/behavior/behavior_session.py` | `goat2.supervisor.behavior` |
| `supervisor/behavior/behavior_store.py` | `goat2.supervisor.behavior` |
| `supervisor/behavior/info_extract.py` | `goat2.supervisor.behavior` |
| `supervisor/behavior/info_types.py` | `goat2.supervisor.behavior` |
| `supervisor/interfaces/__init__.py` | `goat2.supervisor.interfaces` |
| `supervisor/interfaces/content_filter.py` | `goat2.supervisor.interfaces` |
| `supervisor/interfaces/telegram_bot.py` | `goat2.supervisor.interfaces` |

Enable DEBUG for a single submodule:

```python
import logging
logging.getLogger("goat2.supervisor.pipeline").setLevel(logging.DEBUG)
# or globally:
logging.getLogger("goat2.supervisor").setLevel(logging.DEBUG)
```

Log levels by event type:
- `DEBUG` — all major operations (task execution, registry lookups, style analysis)
- `INFO` — pipeline events (DAG start, wave execution, validation, synthesis)
- `WARNING` — errors, fallbacks, missing data, truncations

---

## Pipeline Architecture

Three execution pipelines, selected by `classify_intent()`:

### Pipeline 1: Conversational (no DAG)
```
intent → classify_intent → CONVERSATIONAL → conv_result (direct_response)
```
- Temperature 0.7 for natural conversation
- CORE_TOOLS (MEMORY_TOOLS + WEB_SEARCH) always available

### Pipeline 2: Analytical (lightweight DAG, ≤2 tasks)
```
intent → classify_intent → ANALYTICAL → decompose_plan (lightweight)
    → WorkflowGraph (≤2 tasks) → synthesize
```

### Pipeline 3: Complex (full DAG)
```
intent → classify_intent → COMPLEX → decompose_plan
    → WorkflowGraph → GoatValidator → critique_results → synthesize_results
    → run_auditor
```
- Full topological wave execution
- Critic fallback: MAJOR/CRITICAL → re-execute upstream tasks with stricter prompts
- DagBridge: polls Redis for `dag:{session_id}:result` written by workflow