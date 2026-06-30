# GOAT 2.0

GOAT 2.0 is a Telegram-based AI agent with a layered memory architecture. Three
independent physical backends are exposed through a single logical abstraction,
giving the agent the right context at the right cost — without static similarity
thresholds, intent classifiers, or hand-tuned cutoffs.

---

## Architecture

### Physical backends

| Tier | Backend | Redis key / storage |
|------|---------|-------------------|
| **Working** | Redis | `goat2:working:{chat_id}` (messages), `cache:{chat_id}:{key}` (L2.5 cache) |
| **Episodic** | ChromaDB | Local persistent collection (`./chroma_data`) |
| **Permanent** | Letta | Core-memory `facts` block via Letta HTTP API |

All three connect lazily on first use and are independent — a Letta outage does
not affect Redis or ChromaDB.

### Logical layers

| Layer | Content | In context | Backed by |
|-------|---------|-----------|-----------|
| **L0 — Identity** | Base persona from `[identity] base_prompt` in `memory.toml` | Always | — (config) |
| **L1 — Facts** | Curated key→value facts in Letta core-memory `facts` block | Always | Permanent |
| **L2 — Working** | Full conversation history for the current chat | Always (capped) | Working / Redis |
| **L2.5 — Session Cache** | TTL cache for L3 search results and tool outputs | When available | Working / Redis |
| **L3 — Episodic** | Semantic long-term memory; injected when structurally relevant | Conditional | Episodic / ChromaDB |

### The mapper (`memory/layers.py`)

`MemoryLayers` is the only interface the orchestrator and bot ever touch. It maps
all five logical layers onto the three physical tiers and exposes typed methods:
`assemble_context`, `get_working_context`, `save_working_context`,
`search_episodic`, `search_episodic_with_cache`, `store_episodic`,
`promote_fact`, and the full L2.5 cache API (`get_cache`, `set_cache`,
`invalidate_cache`, `clear_cache`, `cache_exists`). The orchestrator and Telegram
bot never import `WorkingMemory`, `EpisodicMemory`, or `PermanentMemory`
directly.

---

## Memory mechanics

### AITS — Adaptive Intent Token Scaling (`memory/aits.py`)

Every turn starts with a dynamic token budget derived from the user message:

```
budget = BUDGET_BASE
       + confidence × BUDGET_CONFIDENCE_MULTIPLIER
       + complexity × BUDGET_COMPLEXITY_MAX_BONUS
(capped at BUDGET_HARD_CAP)
```

**Confidence** (0–1): set-membership over the query's word tokens against three
word lists — high (interrogative/analytical cues: `what`, `how`, `why`, …),
medium (auxiliary verbs), low (greetings). High cues score 0.8–1.0; medium 0.5;
low 0.2; unknown words default 0.5.

**Complexity** (0–1): `(len(query) / 200) × 0.7 + connector_bonus × 0.3`, where
`connector_bonus` fires on multi-part connectors (`and`, `or`, `;`, etc.).

Default knobs from `config/memory.toml`: base 2000, multiplier 4000, complexity
bonus 2000, hard cap 12000. A greeting yields ~2000 tokens; a detailed
multi-part question approaches 12000.

### Priority-inverted L2/L3 budget split (`memory/context_budget.py`)

After L0+L1 tokens are subtracted, `allocate_context_budget` splits the
remainder with L3 having **first claim**:

```python
l3_guarantee = L3_MIN_GUARANTEE_TOKENS   # 1200 by default
l2_cap       = available - l3_guarantee  # L2 takes the rest
```

L2 has a floor (`L2_FLOOR_TOKENS = 500`) that wins only on pathologically small
budgets. On every realistic AITS budget, L3 is guaranteed at least 1200 tokens
regardless of how long L2 has grown.

### Unconditional prefetch + dynamic gap filtering

The orchestrator runs an L3 search at the start of **every turn** with no
confidence-threshold gate (see `orchestrator/orchestrator.py`, step 2 comment:
"Pre-search confidence gating is removed"). The search routes through
`search_episodic_with_cache` (L2.5-backed, TTL 300 s) so an identical query
within the same session is served from Redis without re-embedding.

Relevance is decided **post-search** by `MemoryLayers._gap_filter`:

```python
scores = [r["score"] for r in results]   # ChromaDB squared-L2 distances, ascending
gaps   = [scores[i+1] - scores[i] for i in range(len(scores) - 1)]
if max(gaps) / mean(gaps) >= L3_GAP_SIGNIFICANCE:   # default 3.0
    keep everything before the largest gap
else:
    inject nothing
```

With fewer than 3 results, a fallback absolute ceiling of 1.5 (squared-L2,
≈ "nearly orthogonal" under unit-norm MiniLM embeddings) applies instead.
The ratio criterion is scale-invariant: as the archive grows with
`l2_full_archive` entries, genuine query clusters produce ratios >> 10 with no
recalibration needed. Calibrated at 3.0 from 12 labeled queries (2026-06-29):
unrelated gap ratios 2.33–2.76 rejected, genuine ratios 3.13–5.13 passed.

### L2.5 session cache (`memory/session_cache.py`)

`SessionCache` stores arbitrary JSON dicts in Redis under
`cache:{chat_id}:{key}` with a configurable TTL (default 300 s). Two things are
currently cached through L2.5:

- **Episodic search results** — keyed by `search:{sha256(query)[:16]}` via
  `MemoryLayers.search_episodic_with_cache`. The orchestrator always routes the
  unconditional prefetch through this path; a cache hit skips the ChromaDB
  embedding call entirely.
- **Tool outputs** — plugins and core tools can cache results explicitly via
  `layers.get_cache` / `layers.set_cache` with deterministic exact-parameter
  keys.

The cache reuses the `WorkingMemory` Redis client — no second connection pool.
Cache entries and conversation history live in different key namespaces
(`cache:*` vs `goat2:working:*`) and never collide.

### Write-through archive (`_archive_turn` in `orchestrator/orchestrator.py`)

After every turn's L2 save, the orchestrator fires `_archive_turn` as
`asyncio.create_task` — fire-and-forget, never blocks the response:

```python
content = f"user: {intent}\nassistant: {reply}"
await layers.store_episodic(chat_id, content, tags=["l2_full_archive"])
```

Every turn lands in L3 automatically, tagged `l2_full_archive` to distinguish
automatic writes from GOAT's curated `store_memory` / `promote_memory` tool
calls. L3 write failure is caught and logged as a warning — it never propagates
to the turn response.

### L2 conversation trim (`MemoryLayers._trim_recent_messages`)

When L2 history exceeds its budget cap, messages are dropped oldest-first with
one exception: the very first message is pinned (it sets the conversation topic)
provided it is small enough (< 25% of the cap). This prevents a long session
from losing all topic context while still prioritising recent messages.

---

## Tool-calling flow

### Single-round structure

The orchestrator enforces a strict two-call maximum per turn: the first LLM call
may request tools; the second call (with tool results appended) never receives a
tool list. Runaway tool loops are structurally impossible.

### Synthesis bridge

After executing tool calls, the orchestrator appends a fixed user turn before
the second LLM call (`_TOOL_SYNTHESIS_BRIDGE` in `orchestrator/orchestrator.py`):

> "Respond to my request based on the tool results above. Only state specific
> numbers, counts, or names if they appear verbatim in the tool output — if a
> figure was not returned by the tools, say so explicitly rather than guessing."

This prevents raw tool-output formatting from dominating generation style (a
context-position proximity effect that the distant system-message persona cannot
overcome).

### Grounding check

Before returning the synthesis reply, `_ungrounded_numbers` extracts all
standalone 2+-digit integers from both the reply and the concatenated tool
results. Any number present in the reply but absent from every tool result
triggers `_run_grounding_correction`: a third LLM call that names the specific
ungrounded figures and asks the model to correct or withdraw them. On LLM error
the prior reply is kept as fallback.

### DSML fallback (deepseek-v4-flash)

Some model versions return tool-call intent as DSML markup in the message
content rather than the standard `tool_calls` field. The orchestrator detects
this via regex, parses the invocations, executes them, and returns the raw tool
output directly — no second LLM call in this path.

### Tool evidence in L2

When tools were called, the saved assistant message in L2 is prefixed with a
compact evidence record:

```
[Tool calls]
called search_memory({"query": "..."}) → - [2026-06-29 21:00] ...
```

Each line is `called {name}({args_preview}) → {result_preview}`. Large-output
tools (`browse_page`, `fetch_content`) store only `[N chars]`; `shell_run`
stores head + tail. This gives future turns verifiable grounding for past tool
use, structurally distinct from a narrated claim.

---

## Memory tools

Three core tools are wired by the Telegram bot at startup:

| Tool | Layer written | When GOAT uses it |
|------|-------------|-------------------|
| `search_memory` | L3 (read) | When the user references something not in the current conversation |
| `store_memory` | L3 (write) | When the user shares something worth keeping across sessions |
| `promote_memory` | L1 (write) | For stable facts that should be in context for every future session |

`promote_memory` is cap-guarded (`L1_FACTS_MAX_TOKENS = 500`): promotion is
refused when the facts block would exceed the cap, nudging GOAT to retire a fact
first. L1 is always-in-context (off the top of every AITS budget), so it must
stay small.

---

## Plugin system

Hot-reload tool plugins live in `tools/goat_skills/`. The `PluginManager`
rescans the directory every 30 s (via a post-init hook in the Telegram bot) and
exposes live plugins to the orchestrator each turn via `registry.plugin_manager.tools`.
New `.py` files dropped into `tools/goat_skills/` load without a restart; files
with import errors are skipped, leaving other plugins unaffected.

Bundled plugins: `browse_page` (Playwright), `fetch_content` (crawl4ai),
`shell_run` (subprocess), `get_memory_metrics`, `get_recent_logs`.

---

## Observability

One `MemoryObservation` is emitted as a JSON log line per turn and fed to the
registry-owned `MemoryAnalytics` aggregator. Fields include: AITS
confidence/complexity/budget, L2.5 cache hit/miss and key, latency per stage
(classify / search / assemble / inject / llm / save), tokens per tier
(L0+L1 / L2 / L3), prefetch outcome, and results found/used. A summary report
is logged every `ANALYTICS_LOG_INTERVAL` requests (default 100).

---

## Project layout

```
goat2/
├── memory/
│   ├── layers.py            # Backend mapper — the only memory interface
│   ├── aits.py              # Adaptive Intent Token Scaling
│   ├── context_budget.py    # Priority-inverted L2/L3 budget split
│   ├── session_cache.py     # L2.5 TTL cache (Redis)
│   ├── promote.py           # L3 → L1 promotion, cap-guarded
│   ├── budget.py            # Token estimation + result-count enforcement
│   ├── config.py            # Reads config/memory.toml; all numeric knobs
│   ├── working/             # Redis-backed working memory (L2)
│   ├── episodic/            # ChromaDB-backed episodic memory (L3)
│   └── permanent/           # Letta-backed permanent memory (L0/L1)
├── orchestrator/
│   └── orchestrator.py      # AITS → prefetch → assemble → LLM → tool round → save + archive
├── telegram_interface/
│   └── bot.py               # Telegram entry point; wires search/store/promote tools
├── registry/
│   └── registry.py          # Lazy DI container; owns all service lifetimes
├── plugins/                 # Hot-reload plugin loader
├── tools/
│   ├── goat_skills/         # Hot-reload plugin tools (drop .py files here)
│   ├── memory_tools.py      # search_memory tool definition
│   ├── memory_writer.py     # store_memory tool definition
│   └── memory_promote.py    # promote_memory tool definition
├── mcp_server/              # Optional MCP introspection server
└── config/
    └── memory.toml          # All tunables: AITS knobs, cache TTL, budget constants, identity
```

---

## Configuration

All memory tunables are in `config/memory.toml`. **Redis and Letta connection
strings have no environment variable override** — edit this file directly for
your setup.

```toml
[identity]
base_prompt = "You are GOAT, a helpful assistant with layered memory."

[working]
storage_url = "redis://localhost:6379/0"

[permanent]
letta_url = "http://localhost:8283"

[aits]
budget_base = 2000
budget_confidence_multiplier = 4000
budget_complexity_max_bonus = 2000
budget_hard_cap = 12000
prefetch_timeout = 0.5          # seconds; episodic search dropped if exceeded

[retrieval_budget]
l3_min_guarantee_tokens = 1200  # L3's guaranteed minimum slice of the budget
l3_gap_significance = 3.0       # max_gap/mean_gap threshold for relevance filter
max_results_per_search = 15

[session_cache]
ttl_seconds = 300               # L2.5 cache TTL

[permanent]
l1_facts_max_tokens = 500       # cap on the Letta facts block
```

LLM provider and Telegram credentials are set via environment variables — see
[SETUP.md](SETUP.md) for the full list.

---

## License

Apache 2.0 — see [LICENSE](LICENSE).
