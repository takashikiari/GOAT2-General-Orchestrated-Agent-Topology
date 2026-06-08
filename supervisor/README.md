# supervisor/ — GOAT 2.0 Workflow Orchestration

```python
from supervisor import GoatSupervisor
from memory.memory_manager import memory_manager

sv = GoatSupervisor(memory_manager=memory_manager)
result = await sv.run("Build a REST API for a todo app")
await sv.finalize_session()   # persist behavioral style to Letta
```

## Pipeline (every turn)

```
init_session(mm)          — first call only; concurrent load: profile + summary + style
mem_turn(mm, intent)      — concurrent: MemoryRouter.recall + info_extract user facts
classify_intent(intent)   — gpt-4o-mini → CONVERSATIONAL | ANALYTICAL | COMPLEX

CONVERSATIONAL → direct_response(history, profile, summary, mem_ctx, behavior_style)
                 model=gpt-4o  temperature=0.7
ANALYTICAL     → decompose_plan("[Lightweight: ≤2 tasks]") → WorkflowGraph
COMPLEX        → decompose_plan() → WorkflowGraph → critique → synthesize
                 all DAG agents: temperature=0.2 (default in _call_llm)

finalize_session() → analyze_style(user_turns) → save_style → Letta goat/persona
```

## Module map

| File | Responsibility |
|------|----------------|
| `types.py` | `AgentTask`, `AgentResult`, `Plan`, `SupervisorResult`, `AgentRunner` |
| `llm_utils.py` | `_call_llm` (default temp=0.2), `_get_client`, `_extract_json` |
| `registry.py` | `AgentRegistry`, `_build_default_registry` — 7 built-in runners |
| `planner.py` | `PLANNER_SYSTEM`, `decompose_plan()` — includes dependency validation |
| `runners.py` | researcher/coder/critic/summarizer/tool_caller runners |
| `runner_memory.py` | `_run_memory` — 3-tier fallback with memory_manager |
| `critique.py` | `critique_results()`, `synthesize_results()` |
| `workflow.py` | `WorkflowGraph` — Kahn's algorithm + concurrent wave execution |
| `classifier.py` | `IntentDepth`, `classify_intent()` |
| `identity.py` | `GOAT_SYSTEM`, `load_user_profile`, `direct_response`, `conv_result` |
| `history.py` | `ConversationHistory`, `load_session_summary` |
| `session.py` | `store_turn` — 3-tier session persistence |
| `session_init.py` | `init_session` — concurrent startup (profile + summary + style) |
| `info_extract.py` | `maybe_store_info` — LLM fact extraction → Letta `human` block |
| `mem_inject.py` | `mem_turn` — concurrent recall + info extract per turn |
| `supervisor.py` | `GoatSupervisor` — assembles all of the above |
| `behavior_profile.py` | `BehaviorProfile` TypedDict, `serialize`/`deserialize` — pure |
| `behavior_analyzer.py` | `analyze_style(turns, existing)` — gpt-4o-mini JSON, temp=0.0 |
| `behavior_store.py` | `load_style`/`save_style` → Letta `goat/persona` block |
| `behavior_mirror.py` | `mirror_instruction(style)` → single-line system-prompt directive |
| `behavior_session.py` | `finalize_behavior` — session-end orchestrator |
| `interfaces/telegram_bot.py` | Telegram adapter — per-chat `GoatSupervisor`; long-polling |

## Behavioral learning flow

```
Session start:  init_session → load_style("goat","persona") → _behavior_style cached
Every response: _system_with_profile(profile, summary, style)
                  → GOAT_SYSTEM + mirror_instruction(style) + profile + summary
Session end:    finalize_session → finalize_behavior(mm, history, current_style)
                  → analyze_style(user_turns) → save_style → PATCH Letta goat/persona
```

## System prompt structure

```
GOAT_SYSTEM: "You are GOAT… Mirror the user's language, tone, and register.
              No filler, no preamble, no sign-offs. Never end with a question."
+ "\nLearned user style — mirror it: formality: casual; tone: technical; …."
+ "\nUser profile:\n{filtered_human_block}"   ← technical keys stripped
+ "\nPrevious sessions:\n{summary}"
```

## Intent routing thresholds

| Depth | Handler | Trigger examples |
|-------|---------|-----------------|
| `CONVERSATIONAL` | `direct_response` (no DAG) | greetings, simple Q&A |
| `ANALYTICAL` | lightweight DAG ≤2 tasks | explain, compare, light coding |
| `COMPLEX` | full DAG + critique | implement, design, multi-step research |

## DAG Dependency Validation

**Added in patch 69:** Planner now validates dependency integrity before passing plan to WorkflowGraph.

**Validation checks:**
1. **Missing dependencies**: Every `depends_on` entry must reference a task ID that exists in the plan.
2. **Circular dependencies**: Detects cycles using DFS (A→B→A or longer chains).

**Recovery strategy:**
1. **Automatic repair**: Invalid `depends_on` references are stripped from tasks.
2. **Fallback plan**: If repair fails (cycle detected), falls back to minimal 2-task plan.
3. **Logging**: All validation failures logged at WARNING level with specific error details.

## Dynamic Model Fallback (Patch 70)

**Problem solved:** Hard-coded model fallbacks (e.g., "gpt-4o") ignored user preferences and caused style clashes.

**Solution:** Configurable priority lists per role with health checks.

**Configuration (goat.toml):**
```toml
# Preferred: list of models in priority order
[agents.planner]
models = ["deepseek-r1", "gpt-4o", "llama-3.3-70b"]

# Backward-compatible: single model
[agents]
researcher = "deepseek-chat"
```

**Behavior:**
1. Checks models in priority order (first = preferred)
2. Validates API key presence for each model's provider
3. Returns first available model that passes health check
4. Raises `ModelUnavailableError` if all models fail (no silent fallback)
5. Logs model switches at INFO level for observability

**Environment variable override:**
```bash
export AGENT_PLANNER_MODEL="gpt-4o"  # Highest priority
```

## Contradiction Detection (Patch 70)

**Problem solved:** DAG validator marked results as `validated=True` even when agents produced contradictory outputs.

**Solution:** Cross-result contradiction detection in `dag_validator.py`.

**Detection method:**
- Scans all result pairs for mutually exclusive claims
- Keyword-based detection (true/false, yes/no, exists/missing, etc.)
- Logs conflicting task IDs and claim snippets at WARNING level

**Validation priority:**
1. `missing_tool_params` — tool called but parameters missing
2. `empty_file_read` — file tool invoked but output empty
3. `empty_generated` — no tool called and output empty
4. **`contradiction`** — NEW: conflicting claims between agents
5. `unverified_execution` — execution role with tool_called=False
6. `source_violation` — source not in role whitelist
7. `net_error` — web search returned error
8. `stale_memory` — memory contains [stale] marker

**Example contradiction:**
```
Task 'tool_caller_1' claims 'file exists' but task 'tool_caller_2' claims 'file missing'
→ DAG marked safe=False, reason="contradiction"
```
