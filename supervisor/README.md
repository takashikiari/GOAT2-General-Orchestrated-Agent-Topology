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
├── planner.py    # Task decomposition
├── critique.py    # Critique and synthesis agents
├── llm_utils.py   # LLM client utilities
├── tool_runner.py # Tool-calling loop
└── __init__.py   # Re-exports for backward compatibility
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