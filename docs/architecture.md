# GOAT 2.0 — Architecture Reference

**G**eneral **O**rchestration of **A**gent **T**asks v2.0  
Async multi-agent system: classify intent → execute DAG → remember results.

## Package layout

```
goat2/
├── config/settings.py            All config, env-var loading, model catalogue (MODELS dict)
├── supervisor/                   16 modules ≤90 lines — routing, DAG, behavioral learning
│   └── interfaces/               Channel adapters wrapping GoatSupervisor.run()
│       └── telegram_bot.py       Telegram bot — per-chat supervisor, long-polling
├── agents/                       BaseAgent ABC + 4 concrete agents (planner/researcher/coder/critic)
├── memory/                       35 modules + router/10 ≤90 lines — 3-tier memory + router
├── tools/                        3 tool definitions (think, calculator, web_search)
└── cli.py                        Chat loop — Redis auto-detect, store_turn, finalize_session
```

## Request pipeline

```
GoatSupervisor.run(intent)
  init_session(mm)       — concurrent: load_user_profile + load_session_summary + load_style
  mem_turn(mm, intent)   — concurrent: MemoryRouter.recall + info_extract facts
  classify_intent()      — gpt-4o-mini → CONVERSATIONAL | ANALYTICAL | COMPLEX

  CONVERSATIONAL → direct_response(messages, profile, summary, mem_ctx, style)
                   model=gpt-4o  temperature=0.7  behavior style injected
  ANALYTICAL     → decompose_plan("[Lightweight: ≤2 tasks]") → WorkflowGraph
  COMPLEX        → decompose_plan() → WorkflowGraph → critique → synthesize
                   all DAG agents: temperature=0.2 (default in _call_llm)

  finalize_session() → analyze_style(user_turns) → save_style → Letta goat/persona
```

## Memory system

| Tier | Backend | Search | Scope |
|------|---------|--------|-------|
| WORKING | DictBackend / Redis | keyword | process, TTL 1 h |
| EPISODIC | ChromaDB HNSW | semantic cosine | session, disk |
| LONG_TERM | Letta REST | semantic server-side | cross-session |

`MemoryManager.recall()` → `MemoryRouter.search()`: classify query → confidence score →
≥0.70 single layer, 0.40–0.69 top-2 sequential, <0.40 full fan-out.  
`search(memory_type=X)` bypasses router and goes direct.

## Behavioral learning

- **Load**: `init_session` reads `goat/persona` Letta block (empty on new agents)
- **Inject**: `_system_with_profile` prepends `mirror_instruction(style)` to `GOAT_SYSTEM`
- **Analyze**: `finalize_behavior` → `analyze_style(turns, existing)` via gpt-4o-mini JSON
- **Persist**: `save_style` → `mm.set_block("goat", "persona", style)` → PATCH Letta block

## Letta agents

Each AgentRole gets one Letta agent (`goat2-{role}`), lazily created with two blocks:

| Block | Purpose | Initial value |
|-------|---------|---------------|
| `persona` | behavioral style profile | `""` (populated by analysis) |
| `human` | user facts from conversations | `""` (populated by info_extract) |

## Telegram interface

`supervisor/interfaces/telegram_bot.py` wraps `GoatSupervisor.run()` behind a Telegram bot.

- Token read from `config/goat.toml [channels] telegram_token` via `TomlConfig.channel_str`.
- One `GoatSupervisor` instance per `chat_id` — conversation history is never mixed across users.
- Text messages only (`filters.TEXT & ~filters.COMMAND`); replies contain `result.summary`.
- Start: `python -m supervisor.interfaces.telegram_bot` (long-polling, no webhook).

## Key invariants

- Max 90 lines/file, single responsibility, all constants `Final[T]`, no `dict[str, Any]`
- `info_extract._merge` and `identity._filter_profile` both strip technical keys (`agent_id`, `passage_id`, `search_key`, `limit`, …) from the human profile
- `GOAT_SYSTEM`: "Mirror the user's language, tone, and register. Never end a response with a question."
- `MemoryRouter` adapts preferences: 70 % static affinity + 30 % observed hit rate per layer
- All behavioral analysis failures are logged at `ERROR` with Letta URL for diagnostics
