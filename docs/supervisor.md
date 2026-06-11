# GoatSupervisor — Architecture Reference

Source: `supervisor/supervisor.py`

---

## Module Overview

GOAT 2.0 top-level orchestrator — unified message handling with autonomous tool selection.

GOAT supervisor manages memory read/write directly across all three tiers (Redis, ChromaDB, Letta).
DAG agents access tools but are restricted to working memory (Redis) with `SESSION_ROLE`.
GOAT validates task success by checking tool parameters — never reports `validated=True` without verification.

---

## Memory Access Architecture

### Supervisor (Full Access)

The `GoatSupervisor` class has unrestricted read/write access to all three memory tiers:
- **WORKING** (Redis): Session-scoped storage with TTL enforcement
- **EPISODIC** (ChromaDB): Semantic search across conversation history
- **LONG_TERM** (Letta): Core memory blocks for agent identity/behavior

Supervisor operations:
- Direct memory read/write during conversational turns
- Post-execution storage of DAG results to all three tiers
- Behavior profile persistence to Letta
- Session initialization and finalization

### DAG Agents (Restricted Access)

Agents executing within `WorkflowGraph` have limited memory access:
- Can ONLY access WORKING memory (Redis) via `task.memory_manager`
- CANNOT directly access ChromaDB or Letta tiers
- Prevents memory pollution from agent-executed operations
- Working memory is session-scoped with automatic TTL enforcement

### Parallel Memory Pipeline

During DAG execution, a concurrent pipeline handles Redis operations:
- Runs alongside task execution without blocking
- Stores intermediate results in working memory
- Enables agents to read/write working context efficiently
- ChromaDB/Letta writes happen post-execution via supervisor

### Automatic Promotion Pipeline

After each `store_turn()`, background tasks promote conversation turns:
- Turn 2+ (messages >= 4): WORKING → EPISODIC, `keep_source=True`
- Turn 3+ (messages >= 6): EPISODIC → LONG_TERM, `keep_source=False`

Promotion includes:
- Duplicate detection in destination tier
- PollutionGuard validation for content quality
- Non-blocking execution via `asyncio.create_task()`

---

## Execution Paths

`run(intent)` routes to one of four paths:

| Path | Trigger | Handler |
|---|---|---|
| **DIRECT BYPASS** | Single-tool query classified by `classify_direct_request` | `_handle_direct_request()` |
| **CONVERSATIONAL** | `IntentDepth.CONVERSATIONAL` | `conv_result()` — LLM + CORE_TOOLS |
| **ANALYTICAL** | `IntentDepth.ANALYTICAL` | DAG with `[Lightweight: ≤2 tasks]` prefix |
| **COMPLEX** | `IntentDepth.COMPLEX` | Full DAG with planner, critic, synthesis |

### Direct Request Bypass (PATCH 71)

Simple single-tool queries bypass the full DAG pipeline:
- `memory_recent`: queries about recent memory items
- `memory_get`: queries retrieving specific named facts by key
- `file_read`: queries reading specific files by path

Classification uses rule-based pattern matching (no LLM calls):
- Rejects queries with multi-step indicators (`and`, `explain`, `analyze`, `compare`)
- Confidence threshold ≥ 0.5 required for bypass
- Falls back to DAG on any uncertainty or error

### DAG Execution Flow (ANALYTICAL / COMPLEX)

```
decompose_plan(plan_ctx, registry)         # planner decomposes intent into tasks
    ↓
prepare_tasks(tasks, memory_manager)       # inject memory_manager + language directive
    ↓
WorkflowGraph.execute(registry, semaphore) # wave-level concurrent execution
    ↓
DagBridge.wait_for_result(session_id)      # poll Redis for dag:{session_id}:result
    ↓
validate_dag_result(dag_detail, results)   # GoatValidator: role/source/tool/hallucination
    ↓
critique_results(plan_ctx, results)        # CriticAgent verdict
    ↓ (if MAJOR/CRITICAL: re-execute with stricter prompts, up to _MAX_CRITIC_RETRIES=2)
synthesize_results(plan_ctx, results, ...) # final answer from verified DAG output
    ↓
run_auditor(results)                       # cross-tool consistency check
```

---

## Critic Fallback (Problema 5)

When the critic returns severity `MAJOR` or `CRITICAL`, the supervisor:
1. Identifies re-runnable tasks (roles in `_STRICTER_SYSTEM_PROMPTS`: researcher, coder, tool_caller)
2. Re-executes them sequentially with a stricter prepended system prompt
3. Re-runs the critic on the new output
4. If still failing after `_MAX_CRITIC_RETRIES` (2) attempts, includes critic warnings in the summary

Stricter prompts are defined in `_STRICTER_SYSTEM_PROMPTS` per role and prepended as
`[STRICT RE-EXECUTION] <override>\n\nOriginal task: <prompt>`.

---

## Validation Requirements

GOAT supervisor validates task success before reporting:
- `tool_called` must be True for execution roles
- `tool_name` must be non-empty
- `raw_output_hash` proves execution occurred
- `source` must match allowed types for the role
- `dag_verified` must be True — ensures LLM synthesizes from real DAG output (not hallucinated)

---

## Temperature Settings

- Supervisor uses temperature 0.5 for accuracy (reduces hallucination in summaries)
- Configured in `config/settings.py` (`SupervisorConfig.temperature`)
- DAG agent temperatures are configured per-role in `agents/`

---

## Registry Injection (Phase 4)

`GoatSupervisor` requires a `ServiceRegistry` parameter — no fallback to old singletons.

Registry is passed through to:
- `classify_intent()` — model selection for classifier
- `mem_turn()` — fact extraction model
- `decompose_plan()` — supervisor model for plan decomposition
- `WorkflowGraph.execute()` — registry passed to runner functions
- `critique_results()` / `synthesize_results()` — model selection
- `conv_result()` — tool access and model selection
- `finalize_behavior()` — Letta base URL for logging

---

## Method Reference

### `__init__(registry: ServiceRegistry)`

Initializes `GoatSupervisor` with the application's `ServiceRegistry`. Sets up:
- `self.registry` — the service container
- `self.memory_manager` — `registry.memory_manager` (all three tiers)
- `self.agent_registry` — aliased to `registry` (which has a `.get(role)` method)
- `self._settings` — `registry.settings`
- `self._semaphore` — `asyncio.Semaphore(settings.supervisor.max_workers)`
- `self._history` — `ConversationHistory | None` (initialized on first `run()`)

### `run(intent: str) -> SupervisorResult`

Unified message handler. See **Execution Paths** above.

Memory access flow:
1. Supervisor receives intent and classifies depth
2. Direct request pre-check for simple single-tool queries
3. For DAG execution: parallel memory pipeline starts for Redis ops
4. DAG agents execute with working memory access only
5. Supervisor validates results via GoatValidator
6. ChromaDB/Letta writes are supervisor-only (not DAG agents)

### `finalize_session() -> None`

Supervisor-only operation. Analyzes session turns, infers communication style, and
persists updated behavior profile to Letta `goat/persona` block via `finalize_behavior()`.
Called at end of conversation session. DAG agents cannot perform this operation.

### `_handle_direct_request(intent, t0) -> SupervisorResult | None`

Classifies intent with `classify_direct_request()` and executes the matching tool directly.
Returns `None` if not a direct request, causing `run()` to fall through to the DAG path.
Falls back to DAG on any tool execution error.

### `_run_memory_pipeline(intent, results) -> None`

Stores DAG results in Redis working memory. Runs concurrently with DAG execution.
Pipeline errors are logged as warnings but never fail execution.

### `_schedule_promotion(turn_count) -> None`

Background task (via `asyncio.create_task`) that calls `memory_manager.promote_turns()`.
Applies WORKING → EPISODIC → LONG_TERM promotion rules based on turn count.

### `register_agent(role, runner) -> None`

Register a pre-built async runner under a role name in the agent registry.

### `make_agent(role, model_key, system_prompt) -> AgentRunner`

Factory: build a simple LLM runner from a model key + system prompt and register it.
The created agent receives `task.memory_manager` during execution (working memory only).

---

## Module-Level Helpers

### `_rerun_failed_tasks(plan, results, registry, semaphore, memory_manager, session_id, verdict)`

Re-executes tasks with roles in `_STRICTER_SYSTEM_PROMPTS` using a stricter prompt.
Tasks are re-executed sequentially (not as a DAG) since they may depend on each other.
Tasks with `error is not None` are skipped.

### `_unverified_summary(results, val_statuses) -> str`

Returns a factual failure message derived entirely from `AgentResult` metadata.
No content is generated or inferred. Called when `dag_verified=False`.

### `_build_metadata_summary(statuses, audit) -> str`

Returns a semicolon-separated metadata string from `ValidationStatus` list and `AuditReport`.
Used as `SupervisorResult.metadata_summary` for logging and debugging.

### `_get_stricter_prompt(task_role, original_prompt) -> str`

Prepends `[STRICT RE-EXECUTION]` + role-specific override to the original task prompt.
Falls back to a generic strictness instruction for roles not in `_STRICTER_SYSTEM_PROMPTS`.

---

## See Also

- `supervisor/README.md` — pipeline architecture, logger namespaces, routing pattern
- `docs/architecture.md` — full system architecture and memory access model
- `supervisor/pipeline/workflow.py` — DAG wave execution
- `supervisor/pipeline/runners.py` — per-role agent runner implementations
- `supervisor/pipeline/goat_validator.py` — DAG result validation before synthesis
- `agents/critique.py` — `critique_results()`, `synthesize_results()`
- `agents/planner_decompose.py` — `decompose_plan()`
