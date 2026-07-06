# GOAT 2.0

Telegram-based AI agent built around a **proactive** layered memory system and a **parallel async multi-agent DAG engine**. Two independent systems coexist: the per-turn memory orchestrator, and the background DAG pipeline the orchestrator can spawn for complex tasks.

The per-turn driver is `Orchestrator.run` (`orchestrator/orchestrator.py`). It talks to memory through one façade — `MemoryLayers` — and never imports a physical backend directly.

---

## What makes it different

- **Proactive, not reactive.** The prefetch daemon starts as the *first* step of every turn and runs in parallel with L0/L1/L2 fetch. Retrieval precedes generation; it is not triggered by the model noticing a gap.
- **Brain activation, not a cache.** L2.5 holds per-chat *thread state* — the centroid of the current topic and the retrieval it produced. A follow-up on the same thread is served from the held activation (no search); the thread breaks only on a consensus shift (semantic drift AND lexical overlap both drop). The LLM builds on a stable reality instead of a flickering one.
- **Three independent physical backends.** Redis (working + activation), ChromaDB (episodic), and Letta (permanent) each connect lazily and fail independently — a Letta outage empties L1 facts and the turn continues.
- **No static thresholds.** The L3 relevance filter is a ratio over the score distribution, scale-invariant by construction. The context budget adapts per turn from two real signals. L3's minimum token slice is guaranteed by construction.
- **Complete fidelity.** Every turn is archived verbatim; the full text of any past exchange is retrievable from a single semantic query — no summary stands between the model and the original words.
- **Background multi-agent DAG.** The orchestrator can spawn a parallel async DAG of specialist agents (planner → researcher → coder → critic → summarizer) for complex tasks, communicate with it mid-run, and retrieve results — while continuing to answer other messages.

---

## Architecture

### Physical backends

| Backend | Class | Storage |
|---------|-------|---------|
| **Working** | `WorkingMemory` (`memory/working/working.py`) | Redis — messages, L2.5 cache, activation state |
| **Episodic** | `EpisodicMemory` (`memory/episodic/episodic.py`) | ChromaDB `PersistentClient` at `./chroma_data` |
| **Permanent** | `PermanentMemory` (`memory/permanent/permanent.py`) | Letta HTTP API — `facts` block on `goat-permanent` |

### Logical layers

| Layer | Content | In context | Backed by |
|-------|---------|-----------|-----------|
| **L0** | Base persona from `[identity] base_prompt` | Always | config |
| **L1** | Curated key→value facts | Always | Permanent / Letta |
| **L2** | Full conversation history (current chat) | Always (capped) | Working / Redis |
| **L2.5** | Per-chat thread state (centroid + held retrieval) | When thread is active | Working / Redis |
| **L3** | Semantic long-term memory | Conditional | Episodic / ChromaDB |

### Service registry (`registry/registry.py`)

`ServiceRegistry` is a lazy DI container — no module-level singleton. Owns every service lifetime: LLM client, the three tiers, `MemoryLayers`, `MemoryAnalytics`, and `PluginManager`. Each backend built on first property access.

---

## The prefetch daemon

On every turn, before the LLM call, the daemon classifies the turn against the per-chat activation:

| State | Trigger | What the daemon does |
|-------|---------|----------------------|
| **cold** | No prior activation, or consensus shift (drift < `drift_cold` AND lexical overlap < `lexical_low`) | Full three-mechanism search; build fresh activation |
| **warm** | `cosine(query, centroid) ≥ drift_warm` | Serve held activation re-scored by recency; skip all search |
| **drift** | Middle band | Targeted thematic refresh against new query |

**Cold — three mechanisms, run concurrently via `asyncio.gather`:**

| Mechanism | Signal | Retrieval |
|-----------|--------|-----------|
| **Temporal** | Completed-past date range | Filtered semantic search |
| **Thematic** | Always (unconditional) | Cached semantic search |
| **Specific-key** | Structural keys (UUID, `word+number`, `turn_`/`goat:`) | Exact get-by-id or content match |

**Merge:** `blended = similarity × 0.6 + recency × 0.3 + access_count × 0.1`. Deduped by `message_id`, sorted best-first, trimmed to `max_results` (20).

**Bounded wait:** daemon is awaited under `asyncio.wait_for(timeout=1.0 s)`. On timeout the turn continues without L3 — the on-demand `search_memory` tool is the fallback.

**Injection:** into the system prompt as `[Context recuperat din istoric]`, never as fake conversation turns.

---

## L2.5 — activation layer (brain thread state)

Stored as one JSON blob per chat in Redis (`activation:{chat_id}`, 7-day cleanup TTL — not a reset). Holds: `centroid` (thread embedding), `merged` (held L3 results), `last_query`, `recent_queries` (rolling window for lexical signal).

- **Warm turns:** serve `rescore_recency(activation.merged, now)` — time attenuates, never resets. No ChromaDB query runs.
- **Enriching writes:** when GOAT stores a fact on-thread (`cosine(content, centroid) ≥ enriching_sim`), the activation is refreshed in-place synchronously before `run()` returns — the next turn sees the new learning folded in.
- **Embeddings:** reuse ChromaDB's bundled ONNX MiniLM — same vector space as retrieval, no extra API call, degrades to `None` on any failure (turn falls back to cold, never breaks).

### Activation thresholds (tuned to MiniLM L6 v2 geometry)

| cosine(query, centroid) | Band | State |
|--------------------------|------|-------|
| 0.80 – 1.00 | Paraphrase + follow-up | **warm** |
| 0.55 – 0.80 | Related, same-entity-different-facet | **drift** |
| < 0.55 | Different target (+ low lexical overlap) | **cold** |

`drift_warm = 0.80`, `drift_cold = 0.55`, `enriching_sim = 0.55`, `lexical_low = 0.15`. Verified by `scripts/threshold_sanity.py` — re-run whenever thresholds or embedding model change.

---

## AITS — Adaptive Intent Token Scaling

```
budget = BUDGET_BASE + confidence × 4000 + complexity × 2000  (cap: 12000)
```

**Confidence** (0–1): set-membership over query tokens against interrogative/analytical cue lists. **Complexity** (0–1): `(len/200) × 0.7 + connector_bonus × 0.3`. A greeting → ~2000 tokens; a detailed multi-part question → ~12000.

**Priority-inverted L2/L3 split:** L3 gets a guaranteed minimum slice (`l3_min_guarantee_tokens = 1200`) first; L2 takes the remainder, AITS-scaled. L3 can never be starved to zero by a long L2.

---

## Tool-calling flow

**Agentic loop:** `AGENTIC_MAX_ITERATIONS` iterations (default 6). Below cap: called WITH tools so the model can chain (read → search → write → verify → synthesise). At cap: called WITHOUT tools — a stuck model must synthesise from what it has. The cap is a hard backstop on cost/latency only; it never inspects content.

**Synthesis bridge:** after each tool batch a fixed user turn is appended before the next LLM call, instructing the model to state only figures that appear verbatim in the tool output.

**DSML fallback:** `deepseek-v4-flash` sometimes returns tool-call intent as DSML markup in content. The orchestrator detects and parses it with a regex, executes directly — no second LLM call.

**L2 evidence:** when tools were called, the saved assistant message is prefixed with `called {name}({args}) → {result_preview}` — one line per call, accumulated across every loop iteration. Future turns have verifiable grounding for past tool use.

---

## Memory tools

| Tool | Layer | Behaviour |
|------|-------|-----------|
| `search_memory` | L3 read | On-demand semantic search; supports `after`/`before` ISO-8601 filters |
| `store_memory` | L3 write | Persist across sessions; may trigger enriching refresh |
| `promote_memory` | L1 write | Stable fact into Letta facts block; cap-guarded upsert-by-key |
| `read_l1` | L1 read | Returns all facts + token usage vs cap |
| `forget_fact` | L1 delete | Remove a key from permanent facts |
| `memory_status` | L1+L2+L3 | Count of facts / messages / episodic entries (total + this chat) |

`l1_facts_max_tokens = 2000` — promotion refused when the facts block would exceed this.

---

## Multi-agent DAG system

The orchestrator can spawn a **parallel async DAG** of specialist agents for complex multi-step tasks. The DAG runs in the background as an `asyncio.Task`; the orchestrator stays fully decoupled and communicates via Redis.

### Agent roles

| Role | Class | Tools |
|------|-------|-------|
| `planner` | `PlannerAgent` | none (pure reasoning) |
| `researcher` | `ResearcherAgent` | `web_search`, `fetch_url`, `memory_search` |
| `coder` | `CoderAgent` | `file_read/write/create/list/search/grep/info/read_lines`, `shell`, `validate_syntax` |
| `critic` | `CriticAgent` | none (structured verdict: ACCEPT / REVISE / REJECT) |
| `summarizer` | `SummarizerAgent` | none (synthesis from context) |
| `tool_caller` | `ToolCallerAgent` | all file tools + `memory_recent/get/store/search` |
| `memory` | `MemoryAgent` | `memory_recent/get/store/search` |

Each agent has an internal agentic loop (`BaseAgent._chat`, up to `max_tool_rounds` iterations) — agents that have tools registered may call them in multiple rounds before returning.

**Web tools** (`WEB_SEARCH`, `FETCH_URL`) use `crawl4ai.AsyncWebCrawler` — the same backend as GOAT's `fetch_content` goat_skill. `WEB_SEARCH` queries DuckDuckGo Lite and returns LLM-ready markdown; `FETCH_URL` fetches a specific URL.

**DAG memory tools** (`MEMORY_*_DAG`) operate on Redis in the `wm:dag:{namespace}:*` namespace — isolated from the main conversation memory.

### DAG execution

```
Orchestrator calls start_workflow(nodes)
    → DagManager.build_graph() + DagManager.start()
    → WorkflowRunner.run() (parallel asyncio.Task per node)
        → each node: fresh BaseAgent + dag_tools injected
            → agent.execute() → BaseAgent._chat() → LLM + tool rounds
    → results written to DagChannel (Redis)
Orchestrator polls via workflow_status() / receives via workflow_send()
```

**Concurrency:** sibling nodes (no mutual dependency) run as concurrent `asyncio.Task`s. `asyncio.Semaphore(max_concurrent=8)`. Per-node timeout via `asyncio.wait_for`. Fail-fast: first node error cancels all in-flight tasks.

**Context propagation:** upstream node output is available to downstream nodes via the shared context dict. Each node gets a snapshot of context at launch time (all deps already resolved at that point).

**Cycle detection:** Kahn's algorithm upfront — `CycleDetected` raised before any node starts.

### Agent ↔ orchestrator communication (mid-task)

Every agent node gets two injected tools bound to the DAG's `DagChannel`:

| Tool | Direction | Behaviour |
|------|-----------|-----------|
| `dag_push_update(message)` | agent → orchestrator | Push progress/partial result to channel outbox; visible via `workflow_status` |
| `dag_check_inbox()` | orchestrator → agent | Non-blocking pop from channel inbox; populated by `workflow_send` |

Fresh agent instance per task (not the cached one) — per-task tools don't leak to concurrent nodes of the same role.

### Orchestrator workflow tools

| Tool | What it does |
|------|-------------|
| `start_workflow(nodes, dag_id?, initial_context?)` | Build DAG from node specs `{id, role, task, deps[]}` and launch as background task |
| `workflow_status(dag_id)` | Node states, results (on completion), and up to 5 recent outbox messages |
| `workflow_send(dag_id, message)` | Push a message to the DAG's inbox (readable by agents via `dag_check_inbox`) |
| `stop_workflow(dag_id)` | Cancel the running DAG task |

### Key files

```
workflow/
├── runner.py         # Parallel async DAG executor (Semaphore + asyncio.wait FIRST_COMPLETED)
├── dag_manager.py    # asyncio.Task lifecycle + DagChannel wiring
├── dag_channel.py    # Redis channel per DAG run (status, inbox, outbox, result)
├── routing.py        # AgentRouter — lazy role→class resolution, get() cached / instantiate() fresh
├── agent_node.py     # make_runner(): DAG context → AgentTask/AgentResult + dag_tools injection
├── registry.py       # WorkflowRegistry — named DAG graph store
├── config.py         # WorkflowConfig (redis_url, ttl, concurrency, timeout)
├── models.py         # TaskNode, DAGGraph, WorkflowResult, NodeRunner
└── errors.py         # CycleDetected, NodeNotFound, WorkflowNotFound, …

tools/
├── dag_tools/        # build_channel_tools(channel, task_id) → dag_push_update, dag_check_inbox
├── agent_file_tools.py   # FILE_READ/WRITE/CREATE/LIST/SEARCH/GREP/INFO/READ_LINES, SHELL
├── agent_dag_tools.py    # MEMORY_RECENT/GET/STORE/SEARCH_DAG (Redis wm:dag: namespace)
├── agent_web_tools.py    # WEB_SEARCH, FETCH_URL (crawl4ai — same as fetch_content goat_skill)
└── types.py              # AgentTool dataclass (duck-type compat with agents.base_agent.ToolDefinition)

agents/
├── base_agent.py     # Abstract BaseAgent: _chat() agentic loop, tool registry, @tool decorator
├── planner.py        # PlannerAgent
├── researcher.py     # ResearcherAgent
├── coder.py          # CoderAgent (+ validate_syntax built-in tool)
├── critic.py         # CriticAgent (+ extract_verdict / is_blocking helpers)
├── summarizer.py     # SummarizerAgent
├── tool_caller.py    # ToolCallerAgent
└── memory_agent.py   # MemoryAgent
```

---

## Plugin system

Hot-reload tool plugins live in `tools/goat_skills/`. `PluginManager` rescans every 30 s (via `post_init` hook). Each turn the orchestrator reads `registry.plugin_manager.tools`; a broken plugin is skipped, last-known-good tools are kept.

Bundled: `browse_page` (Playwright), `fetch_content` (crawl4ai), `shell_run`, `read_file`, `write_file`, `get_memory_metrics`, `get_recent_logs`. Drop a `.py` with `build(registry) -> list[ToolDefinition]` to add a tool without restart.

---

## Configuration

**`config/memory.toml`** — all memory tunables (Redis/Letta URLs, AITS, prefetch, budget, cache, activation, identity). Read with `tomllib.load`; no env-var override.

**`config/settings.py`** — LLM provider + Telegram credentials from env vars (`DEEPSEEK_API_KEY`, `MODEL_NAME`, `BASE_URL`, `TELEGRAM_BOT_TOKEN`). Per-agent model overrides via `GOAT_AGENT_{ROLE}_MODEL`, `GOAT_AGENT_{ROLE}_TOOL_CALLING`, `GOAT_AGENT_{ROLE}_TEMPERATURE`.

Key tunables:

```toml
[identity]
base_prompt = "You are GOAT, a helpful assistant with layered memory."

[permanent]
l1_facts_max_tokens = 2000        # cap on Letta facts block

[aits]
budget_base = 2000
budget_hard_cap = 12000

[prefetch]
timeout = 1.0                     # asyncio.wait_for bound; graceful degradation on exceed
max_results = 20
score_similarity_weight = 0.6
score_recency_weight = 0.3
score_access_weight = 0.1

[retrieval_budget]
l3_min_guarantee_tokens = 1200    # L3's guaranteed minimum slice (priority-inverted)
l3_gap_significance = 3.0         # max_gap/mean_gap for raw-result gap filter

[activation]
ttl_seconds = 604800              # 7-day cleanup; NOT a reset
drift_warm = 0.80
drift_cold = 0.55
enriching_sim = 0.55
lexical_low = 0.15
```

See **[SETUP.md](SETUP.md)** for environment variables and startup verification.

---

## Setup

```bash
git clone https://github.com/takashikiari/GOAT2-General-Orchestrated-Agent-Topology.git
cd GOAT2-General-Orchestrated-Agent-Topology
./run.sh          # Windows: run.bat
```

The first launch detects a missing `goat2.toml` and starts the wizard automatically.

### Wizard (`setup/wizard.py`)

Interactive TUI (built on `questionary` + `rich`). Generates two files:

| File | Contents |
|------|----------|
| `goat2.toml` | Provider choice, model, service URLs, feature flags |
| `.env` | API keys — never commit this file |

Re-run at any time to change provider or keys:
```bash
python3 setup/wizard.py --reconfigure
```

### Supported providers (`setup/providers.toml`)

| Provider | Recommended | Notes |
|----------|-------------|-------|
| **DeepSeek** | ✓ | Best price/quality ratio |
| OpenAI | | GPT-4o, o1, o3-mini |
| Anthropic | | Claude Opus/Sonnet/Haiku |
| Groq | | Very fast inference, free tier |
| Ollama | | Runs locally, no API key |
| OpenRouter | | 200+ models via one key |
| Google Gemini | | Free tier at aistudio.google.com |

### Optional services (`setup/services.toml`)

| Service | Required | Purpose |
|---------|----------|---------|
| **Telegram Bot** | Yes | Primary interface — create via @BotFather |
| Redis | Recommended | DAG state, session memory, update notifications |
| ChromaDB | Recommended | Long-term vector memory across sessions |
| Letta | No | Advanced stateful memory backend (alternative to ChromaDB) |

### Pre-flight checks (`setup/checks.py`)

Run automatically by `run.sh` and the wizard before every launch. Also standalone:

```bash
python3 setup/checks.py
```

Checks: Python ≥ 3.11, git, pip, Redis reachability, ChromaDB import, disk space (500 MB). Required failures abort; optional failures warn and continue.

### Updater (`setup/updater.py`)

Checks GitHub Releases, shows changelog, runs `git pull` + `pip install` + restart.

```bash
python3 setup/updater.py          # interactive
python3 setup/updater.py --check  # check only, no install
```

Also triggered from Telegram: send `/update` to your bot.

### Rollback (`setup/rollback.py`)

Reverts to any prior release tag and reinstalls dependencies for that version.

```bash
python3 setup/rollback.py            # interactive picker
python3 setup/rollback.py --to v0.2.1
python3 setup/rollback.py --list
```

---

## Benchmark results

Full suite — 116 unit tests pass. Live benchmark (`python3 -m benchmark`) against real Redis + ChromaDB:

| Dataset | Cases | Accuracy | Grounded |
|---------|-------|----------|----------|
| memory_recall | 10 | **100%** | 100% |
| temporal | 5 | **100%** | 100% |
| multi_turn | 3 | **100%** | 100% |
| distractor_15 | 3 | **100%** | 100% |
| distractor_25 | 3 | **100%** | 100% |
| distractor_30 | 3 | **100%** | 100% |
| distractor_200 | 3 | **100%** | 100% |
| distractor_800 | 3 | 66.7% | 100% |

**Key findings from distractor stress tests** (L3-only, multi-sentence paragraphs, non-guessable answers, lexical decoys):
- Grounding stays 100% on d25–d800: when the system can't retrieve the fact it says so — never guesses.
- Failures are ranking failures, not volume failures: d400 (800 L3 entries) succeeds; d800 failures occur when lexical decoys share the exact semantic sub-domain of the target.
- No degradation curve: accuracy does not decrease monotonically with N — the limit is semantic, not computational.

---

## Project layout

```
goat2/
├── memory/
│   ├── layers.py                  # Backend mapper — sole memory interface (L0-L3 + L2.5)
│   ├── activation.py              # L2.5 turn/write classification + activation store
│   ├── aits.py                    # Adaptive Intent Token Scaling
│   ├── context_budget.py          # Priority-inverted L2/L3 budget split
│   ├── result_merger.py           # Dedupe + blended score across prefetch mechanisms
│   ├── query_classifier.py        # 3-mechanism prefetch classification
│   ├── temporal_parser.py         # dateparser completed-past range extraction
│   ├── session_cache.py           # L2.5 cold-path TTL cache (Redis)
│   ├── promote.py                 # L3 → L1 promotion, cap-guarded
│   ├── working/working.py         # Redis-backed working memory + activation store
│   ├── episodic/                  # ChromaDB lifecycle, search, embed_query, queries mixin
│   └── permanent/permanent.py     # Letta-backed permanent memory
├── orchestrator/
│   ├── orchestrator.py            # Per-turn driver: classify → prefetch → AITS → assemble → LLM → tool loop → save
│   └── tools.py                   # Orchestrator-facing ToolDefinition type
├── workflow/                      # Parallel async multi-agent DAG engine
├── agents/                        # 7 BaseAgent subclasses (planner/researcher/coder/critic/summarizer/tool_caller/memory)
├── tools/
│   ├── goat_skills/               # Hot-reload orchestrator plugins (browse_page, fetch_content, shell_run, …)
│   ├── dag_tools/                 # Per-task agent↔DAG channel tools (dag_push_update, dag_check_inbox)
│   ├── agent_file_tools.py        # Agent file/shell tool constants
│   ├── agent_dag_tools.py         # Agent Redis working-memory tool constants
│   ├── agent_web_tools.py         # WEB_SEARCH + FETCH_URL (crawl4ai)
│   ├── memory_tools.py            # search_memory
│   ├── memory_writer.py           # store_memory
│   ├── memory_promote.py          # promote_memory
│   └── memory_manager.py          # read_l1, forget_fact, memory_status
├── config/
│   ├── memory.toml                # All memory + DAG tunables
│   ├── settings.py                # LLM/Telegram env vars + ModelSpec + Settings (per-agent)
│   ├── agent_types.py             # AgentTask, AgentResult, AgentRunner, Plan
│   └── timeouts.py                # TURN_TIMEOUT
├── utils/llm_utils.py             # _get_client (cached per provider), _call_llm, _extract_json
├── registry/registry.py           # Lazy DI container
├── plugins/plugin_manager.py      # Hot-reload plugin orchestrator
├── setup/
│   ├── wizard.py                  # Interactive first-run TUI (questionary + rich); --reconfigure to re-run
│   ├── checks.py                  # Pre-flight: Python/git/pip/Redis/ChromaDB/disk — run by run.sh
│   ├── updater.py                 # GitHub Releases check + git pull + pip install + restart
│   ├── rollback.py                # Revert to any prior release tag (interactive picker or --to <tag>)
│   ├── providers.toml             # Supported LLM providers (DeepSeek, OpenAI, Anthropic, Groq, Ollama, OpenRouter, Gemini)
│   ├── services.toml              # Optional services (Telegram, Redis, ChromaDB, Letta)
│   ├── templates/goat2.default.toml  # Config template the wizard writes from
│   └── requirements.txt           # questionary + rich (wizard-only deps)
├── mcp_server/                    # Optional standalone MCP introspection server
├── scripts/                       # threshold_sanity.py, enriching_check.py, repair_episodic.py
├── benchmark/                     # Live benchmark suite
└── tests/                         # 116 unit tests (faked backends, no external services)
```

---

## License

Apache 2.0 — see [LICENSE](LICENSE).
