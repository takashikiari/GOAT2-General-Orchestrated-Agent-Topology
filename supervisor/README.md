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
                 DAG agents default temperature=0.2  (_call_llm default)
```

## Module map

| File | Responsibility |
|------|----------------|
| `types.py` | `AgentTask`, `AgentResult`, `Plan`, `SupervisorResult`, `AgentRunner` |
| `llm_utils.py` | `_call_llm` (default temp=0.2), `_get_client`, `_extract_json` |
| `registry.py` | `AgentRegistry`, `_build_default_registry` — 7 built-in runners |
| `planner.py` | `PLANNER_SYSTEM`, `decompose_plan()` |
| `runners.py` | researcher/coder/critic/summarizer/tool_caller runners; `needs_internet(task)` routes search intents to `web_search` with `tool_choice="required"` |
| `runner_memory.py` | `_run_memory` — 3-tier fallback with memory_manager |
| `critique.py` | `critique_results()`, `synthesize_results()` |
| `workflow.py` | `WorkflowGraph` — Kahn's algorithm + concurrent wave execution |
| `classifier.py` | `IntentDepth`, `classify_intent()` |
| `identity.py` | `GOAT_SYSTEM`, `load_user_profile`, `direct_response`, `conv_result`; `_filter_profile` strips technical metadata keys |
| `history.py` | `ConversationHistory`, `load_session_summary` |
| `session.py` | `store_turn` — 3-tier session persistence |
| `session_init.py` | `init_session` — concurrent startup (profile + summary + style) |
| `info_extract.py` | `maybe_store_info` — LLM fact extraction → Letta `human` block |
| `mem_inject.py` | `mem_turn` — concurrent recall + info extract per turn |
| `supervisor.py` | `GoatSupervisor` — assembles all of the above |
| `behavior_profile.py` | `BehaviorProfile` TypedDict, `serialize`/`deserialize` — pure |
| `behavior_analyzer.py` | `analyze_style(turns, existing)` — gpt-4o-mini JSON, temp=0.0 |
| `behavior_store.py` | `load_style`/`save_style` → Letta `goat/persona` block; returns `bool` |
| `behavior_mirror.py` | `mirror_instruction(style)` → single-line system-prompt directive |
| `behavior_session.py` | `finalize_behavior` — session-end orchestrator; logs ERROR on write failure |
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
