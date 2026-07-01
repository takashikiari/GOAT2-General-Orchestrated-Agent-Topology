# GOAT 2.0

GOAT 2.0 is a Telegram-based AI agent built around a **proactive** layered
memory system. The core distinction from a standard RAG setup: memory retrieval
runs *before* the LLM responds, on every turn, independent of the query's
content. A one-word ambiguous message still triggers a semantic search of past
sessions and injects whatever is structurally relevant into the prompt — the
model never has to ask "do I remember this?" because retrieval already
happened.

The per-turn driver is `Orchestrator.run` (`orchestrator/orchestrator.py`). It
talks to memory through one façade — `MemoryLayers` in `memory/layers.py` — and
never imports a physical backend directly.

---

## What makes it different

- **Proactive, not reactive.** The prefetch daemon (`Orchestrator._prefetch_daemon`)
  starts as the *first* step of every turn (`asyncio.create_task` at
  `orchestrator.py:247`) and runs in parallel with the L0/L1/L2 fetch. Retrieval
  precedes generation; it is not triggered by the model noticing a gap.
- **Three independent physical backends.** Redis (working), ChromaDB (episodic),
  and Letta (permanent) each connect lazily and fail independently — a Letta
  outage empties L1 facts and the turn continues (`memory/layers.py:80`).
- **No static thresholds.** The L3 relevance filter is a ratio over the score
  distribution, scale-invariant by construction (`MemoryLayers._gap_filter`,
  `layers.py:353`). The context budget adapts per turn from two real signals
  (`memory/aits.py`). L3's minimum token slice is guaranteed *by construction*,
  not tuned (`memory/context_budget.py`).
- **Complete fidelity.** Nothing is compressed or extracted. Every turn is
  archived verbatim (`_archive_turn`, `orchestrator.py:173`), so the full text of
  any past exchange is retrievable from a single semantic query — no summary
  stands between the model and the original words.

---

## Architecture

### Physical backends

| Backend | Class | Storage | Key / location |
|---------|-------|---------|----------------|
| **Working** | `WorkingMemory` (`memory/working/working.py`) | Redis | `goat2:working:{chat_id}` (messages), `cache:{chat_id}:{key}` (L2.5) |
| **Episodic** | `EpisodicMemory` (`memory/episodic/episodic.py`) | ChromaDB `PersistentClient` | local collection at `EPISODIC_STORAGE_PATH` (`./chroma_data`), name `episodic_memory` |
| **Permanent** | `PermanentMemory` (`memory/permanent/permanent.py`) | Letta HTTP API | core-memory `facts` block on agent `goat-permanent` |

All three connect lazily on first use. The Redis client is built in
`WorkingMemory._get_client`; ChromaDB in `EpisodicMemory._get_collection`; Letta
via `httpx.AsyncClient` in `PermanentMemory._get_http`. ChromaDB's sync API is
bridged with `asyncio.to_thread`. The episodic collection has no `hnsw:space`
override, so `search` returns squared-L2 distances in `result["score"]`
(lower = closer).

### Logical layers

| Layer | Content | In context | Backed by |
|-------|---------|-----------|-----------|
| **L0 — Identity** | Base persona from `[identity] base_prompt` | Always | config (`memory.toml`) |
| **L1 — Facts** | Curated key→value facts in Letta `facts` block | Always | Permanent |
| **L2 — Working** | Full conversation history for the current chat | Always (capped) | Working / Redis |
| **L2.5 — Session Cache** | TTL cache for L3 search results + tool outputs | When available | Working / Redis |
| **L3 — Episodic** | Semantic long-term memory; injected when relevant | Conditional | Episodic / ChromaDB |

### The mapper (`memory/layers.py`)

`MemoryLayers` is the only memory interface the orchestrator and bot touch. It
maps the five logical layers onto the three physical tiers and exposes typed
methods: `assemble_context`, `get_working_context`, `save_working_context`,
`search_episodic`, `search_episodic_with_cache`, `store_episodic`,
`find_by_keys`, `bump_access`, `promote_fact`, and the full L2.5 cache API
(`get_cache`, `set_cache`, `invalidate_cache`, `clear_cache`, `cache_exists`).
Neither `orchestrator.py` nor `telegram_interface/bot.py` imports
`WorkingMemory`, `EpisodicMemory`, or `PermanentMemory`.

### Service registry (`registry/registry.py`)

`ServiceRegistry` is a lazy DI container that owns every service lifetime —
LLM client, the three tiers, `MemoryLayers`, `MemoryAnalytics`, and
`PluginManager`. It is a class instance passed by the caller, **not** a
module-level singleton (the zero-singleton rule). Each backend is built on
first property access.

---

## The prefetch daemon

This is the core differentiator. It runs on every turn, before the LLM call.

### 1. Started first, in parallel with L0/L1/L2

`run()` opens with `asyncio.create_task(self._prefetch_daemon(chat_id, intent))`
(`orchestrator.py:247`). Immediately after, it starts two more tasks —
`layers.get_identity_and_facts()` (L0+L1) and `layers.get_working_context()`
(L2) at `orchestrator.py:250-251` — so the daemon's L3 search overlaps the tier
fetches with real concurrency, not sequentially. The AITS classify (CPU,
instant) runs during this overlap.

### 2. Three mechanisms, evaluated independently, no gate

The daemon classifies the query three ways and runs every mechanism that scores
above zero. There is **no confidence gate** on whether prefetch runs at all —
the timeout is the only blocker. Classification lives in
`memory/query_classifier.py`:

| Mechanism | Signal | Source | Retrieval |
|-----------|--------|--------|-----------|
| **Temporal** | A completed-past date range | `extract_temporal_range` (`memory/temporal_parser.py`, `dateparser`, `STRICT_PARSING`) | `search_episodic(after, before)` — filtered semantic search |
| **Thematic** | Always 1.0 | none — unconditional | `search_episodic_with_cache` — cached semantic search (carries `cache_key`) |
| **Specific-key** | Structural keys present | `extract_structural_keys` (`query_classifier.py:38`) — UUID, `agent-{uuid}`, `word+number`, `turn_`/`goat:` | `find_by_keys` — UUID get-by-id + content `$contains`, exact match (`score=0.0`) |

The temporal mechanism uses a grammatical date parser, **not** a keyword list.
The "completed-past" rule (`temporal_parser.py:59`, `before < now`) means
`azi`/today yields `None` (its day-range ends tonight) while `ieri`/yesterday
or an absolute past date yields that day's range — so `azi` used
non-temporally does not false-trigger it. `STRICT_PARSING` rejects ambiguous
month-only tokens (RO `mai` = adverb "still" vs month "May"). The specific-key
mechanism matches only structural forms — there is no regex over natural
language.

The mechanisms run concurrently via
`asyncio.gather(*tasks, return_exceptions=True)` (`orchestrator.py:434`); a
single mechanism that raises is skipped, the rest still contribute.

### 3. Merge and score

`merge_results` (`memory/result_merger.py:71`) dedupes across the three result
groups by `message_id`, scores each result, and sorts best-first. The blend is
fixed by config weights (`[prefetch]`):

```
blended = similarity * 0.6 + recency * 0.3 + access_count * 0.1
```

- `similarity = 1 / (1 + distance)` — Chroma squared-L2 distance → 0–1; exact
  structural matches carry distance 0 → similarity 1.0.
- `recency` — storage-timestamp age over `recency_window_days` (30 d), 1.0 just
  stored → 0.0 past the window.
- `access_count` — retrieval popularity, capped at `access_count_ref` (10),
  bumped on retrieval by `bump_access`.

Each merged result carries a `blended_score` field. The list is trimmed to
`PREFETCH_MAX_RESULTS` (15). Accessed records have their `access_count`/
`last_accessed_ts` bumped fire-and-forget (`orchestrator.py:459`).

### 4. Bounded wait, graceful degradation

The daemon is awaited under
`asyncio.wait_for(prefetch_task, timeout=PREFETCH_TIMEOUT)` (0.5 s,
`orchestrator.py:276`). On timeout or any exception the task is cancelled and
the turn continues without L3 (`orchestrator.py:281-287`) — the on-demand
`search_memory` tool is the fallback. A successful run returns
`(results, cache_hit, cache_key)`.

### 5. Injection — system prompt, not conversation history

Results are passed to `assemble_context`, which fits them into the L3 token
slice and emits them as a `[Context recuperat din istoric]` block
(`layers.py:290`). Because the daemon pre-scored the results, `assemble_context`
detects the `blended_score` field and sorts best-first *instead of* running the
gap filter (`layers.py:281-285`). The block is joined into `system_content`
(`orchestrator.py:305`) — part of the system prompt. The conversation history
sent to the API is only `[{system}, {user}]` (`orchestrator.py:314`); L3 never
appears as fake prior turns.

### 6. Relevance for non-daemon results — the gap filter

When `assemble_context` receives **raw** results with no `blended_score` (the
on-demand `search_memory` path, or direct tests), it falls back to
`MemoryLayers._gap_filter` (`layers.py:353`):

```python
scores = [r["score"] for r in results]      # squared-L2 distances, ascending
gaps   = [scores[i+1] - scores[i] for i in range(len(scores) - 1)]
if max(gaps) / mean(gaps) >= L3_GAP_SIGNIFICANCE:   # default 3.0
    keep everything before the largest gap
else:
    inject nothing
```

With fewer than 3 results, an absolute ceiling of 1.5 applies instead (squared-L2
≈ "nearly orthogonal" under unit-norm MiniLM embeddings). The ratio criterion is
scale-invariant: as the archive grows, genuine query clusters produce ratios
>> 10 with no recalibration. Calibrated at 3.0 from 12 labeled queries
(2026-06-29): unrelated ratios 2.33–2.76 rejected, genuine ratios 3.13–5.13
passed.

---

## AITS — Adaptive Intent Token Scaling (`memory/aits.py`)

Every turn computes a dynamic token budget from two signals in the user
message:

```
budget = BUDGET_BASE
       + confidence × BUDGET_CONFIDENCE_MULTIPLIER
       + complexity × BUDGET_COMPLEXITY_MAX_BONUS
(capped at BUDGET_HARD_CAP)
```

**Confidence** (0–1): set-membership over the query's word tokens (split on
whitespace, stripped of punctuation — no regex) against two lists — high
(interrogative/analytical cues: `what`, `how`, `why`, `cum`, `dece`, `când`, …)
and medium (auxiliary verbs). Empty query → 0.2; high cues → 0.8–1.0 scaled by
cue count; medium cues → 0.5; any other statement → 0.5. There is **no
low-confidence list** — greetings and short turns default to 0.5, not 0.2.

**Complexity** (0–1): `(len(query) / 200) × 0.7 + connector_bonus × 0.3`, capped
at 1.0. `connector_bonus` fires on multi-part connectors (` and `, ` or `,
`și`, `sau`, ` plus `, `;`, `,`).

Default knobs (`config/memory.toml [aits]`): base 2000, multiplier 4000,
complexity bonus 2000, hard cap 12000. A greeting yields ~2000 tokens; a
detailed multi-part question approaches 12000.

### Priority-inverted L2/L3 split (`memory/context_budget.py`)

After L0+L1 tokens are subtracted, `allocate_context_budget` splits the
remainder with **L3 first**:

```python
l3_guarantee = L3_MIN_GUARANTEE_TOKENS   # 1200 by default
l2_cap       = available - l3_guarantee  # L2 takes the rest, AITS-scaled
```

L2 has a floor (`L2_FLOOR_TOKENS = 500`) that wins only on pathologically small
budgets. On every realistic AITS budget, L3 is guaranteed at least 1200 tokens
regardless of how long L2 has grown — L2 can no longer eat the whole budget and
starve L3 to zero. L3 is fit into the remainder after L0+L1+L2 in
`assemble_context` (`layers.py:279`); `l3_used` is how many results fit.

---

## Memory mechanics

### Write-through archive (`_archive_turn`, `orchestrator.py:173`)

After every turn's L2 save, the orchestrator fires `_archive_turn` as
`asyncio.create_task` — fire-and-forget, never blocks the response, never
raises:

```python
content = f"user: {intent}\nassistant: {reply}"
await layers.store_episodic(chat_id, content, tags=["l2_full_archive"])
```

Every turn lands in L3 verbatim, tagged `l2_full_archive` to distinguish
automatic writes from GOAT's curated `store_memory` / `promote_memory` calls.
`store_episodic` seeds `access_count=0` and `last_accessed_ts` on write
(`layers.py:130-135`) so the merge-score terms exist from the first retrieval.

### L2 conversation trim (`MemoryLayers._trim_recent_messages`, `layers.py:294`)

When L2 history exceeds its cap, messages are dropped oldest-first with one
exception: the very first message (the topic-setter) is pinned provided it is
small (< 25% of the cap). This prevents a long session from losing all topic
context while still prioritising recent messages.

---

## Tool-calling flow

### Single-round structure

The orchestrator enforces a strict two-call maximum per turn. The first LLM
call may request tools (`orchestrator.py:321-323`); the second call, with tool
results appended, is made **without** a `tools` kwarg (`orchestrator.py:497`).
Runaway tool loops are structurally impossible.

### Synthesis bridge (`_TOOL_SYNTHESIS_BRIDGE`, `orchestrator.py:496`)

After executing tool calls, a fixed user turn is appended before the second LLM
call:

> "Respond to my request based on the tool results above. Only state specific
> numbers, counts, or names if they appear verbatim in the tool output — if a
> figure was not returned by the tools, say so explicitly rather than guessing."

This prevents raw tool-output formatting from dominating generation style (a
context-position proximity effect the distant system-message persona cannot
overcome).

### Grounding check (`_ungrounded_numbers`, `orchestrator.py:154`)

Before returning the synthesis reply, all standalone 2+-digit integers in the
reply are compared against those in the concatenated tool results. Any number
present in the reply but absent from every tool result triggers
`_run_grounding_correction` — a third LLM call that names the specific
ungrounded figures and asks the model to correct or withdraw them. On LLM
error the prior reply is kept as fallback.

### DSML fallback (`_run_dsml_tool_round`, `orchestrator.py:543`)

`deepseek-v4-flash` sometimes returns tool-call intent as DSML markup in the
message content instead of the standard `tool_calls` field. The orchestrator
detects this with a regex, parses the invocations, executes them, and returns
raw tool output directly — no second LLM call on this path.

### Tool evidence in L2 (`_compact_tool_summary`, `orchestrator.py:133`)

When tools were called, the saved assistant message in L2 is prefixed with a
compact evidence record, one line per call:
`called {name}({args_preview}) → {result_preview}`. Large-output tools
(`browse_page`, `fetch_content`) store only `[N chars]`; `shell_run` stores
head + tail. This gives future turns verifiable grounding for past tool use,
structurally distinct from a narrated claim.

---

## Memory tools

Three core tools are wired by the Telegram bot at startup (`bot.py:47-50`):

| Tool | Layer | When GOAT uses it |
|------|-------|-------------------|
| `search_memory` (`tools/memory_tools.py`) | L3 read | User references something not in the current conversation; supports `after`/`before` ISO-8601 filters |
| `store_memory` (`tools/memory_writer.py`) | L3 write | User shares something worth keeping across sessions |
| `promote_memory` (`tools/memory_promote.py` → `memory/promote.py`) | L1 write | A stable fact that should be in context for every future session |

`promote_memory` is cap-guarded: promotion is upsert-by-key, and refused when
the formatted facts block would exceed `L1_FACTS_MAX_TOKENS` (500,
`memory/promote.py:53`). L1 is always-in-context (off the top of every budget),
so it must stay small and curated.

---

## Plugin system

Hot-reload tool plugins live in `tools/goat_skills/`. The registry-owned
`PluginManager` (`plugins/plugin_manager.py`) rescans the directory every 30 s
(via the `post_init` hook in `telegram_interface/_plugin_scanner.py`, which also
pre-warms ChromaDB with `EpisodicMemory.warmup()`). Each turn the orchestrator
reads `registry.plugin_manager.tools` (`orchestrator.py:219`); `scan()`
atomically swaps the tool list, and a plugin that fails to build is skipped
while its last-known-good tools are kept — a broken edit never wipes a working
tool.

Bundled plugins: `browse_page` (Playwright), `fetch_content` (crawl4ai),
`shell_run` (subprocess), `get_memory_metrics`, `get_recent_logs`. Drop a `.py`
file exposing `build(registry) -> list[ToolDefinition]` into `tools/goat_skills/`
to add a tool without a restart.

---

## Observability

One `MemoryObservation` (`memory/observability.py`) is emitted as a JSON log
line per turn, built by `ObservationCollector` (`memory/observability_collector.py`)
and aggregated by the registry-owned `MemoryAnalytics`
(`memory/analytics.py`). Fields include: AITS confidence/complexity/budget,
L2.5 cache hit/miss + key, latency per stage (`classify` / `search` / `assemble`
/ `inject` / `llm` / `save` / `total`), tokens per tier (L0+L1 / L2 / L3),
prefetch outcome (attempted / succeeded / timeout / blocks injected / blocks
used), and results found/used. A summary report is logged every
`ANALYTICS_LOG_INTERVAL` requests (default 100).

The `intent_category` field is a coarse, analytics-only label derived from the
confidence tier (`observability_collector.py:111`): `recall` (≥ 0.4),
`greeting` (< 0.3), else `conversational`. It labels the analytics tier and never
gates prefetch.

---

## Project layout

```
goat2/
├── memory/
│   ├── layers.py                    # Backend mapper — the only memory interface (L0-L3 + L2.5)
│   ├── aits.py                      # Adaptive Intent Token Scaling (confidence + complexity)
│   ├── context_budget.py            # Priority-inverted L2/L3 budget split
│   ├── result_merger.py              # Prefetch merge: dedupe + blended score (0.6/0.3/0.1)
│   ├── query_classifier.py          # 3-mechanism prefetch classification (temporal/thematic/specific-key)
│   ├── temporal_parser.py           # dateparser completed-past range extraction
│   ├── session_cache.py             # L2.5 TTL cache (Redis)
│   ├── promote.py                   # L3 → L1 promotion, cap-guarded
│   ├── budget.py                    # Token estimation + result-count enforcement
│   ├── observability.py             # MemoryObservation dataclass (per-turn JSON)
│   ├── observability_collector.py   # Per-turn observation builder + intent category
│   ├── analytics.py                 # Registry-owned metrics aggregator + report
│   ├── config.py                    # Reads config/memory.toml; all numeric knobs
│   ├── working/working.py            # Redis-backed working memory (L2)
│   ├── episodic/
│   │   ├── episodic.py               # ChromaDB lifecycle + store/search (L3)
│   │   ├── queries.py                # find_by_keys, bump_access, get_recent/count/delete (L3 mixin)
│   │   └── warmup.py                 # Collection pre-warm at startup
│   └── permanent/permanent.py        # Letta-backed permanent memory (L0/L1)
├── orchestrator/
│   ├── orchestrator.py               # Per-turn driver: prefetch → AITS → assemble → LLM → tool round → save+archive
│   └── tools.py                      # ToolDefinition type
├── telegram_interface/
│   ├── bot.py                        # Telegram entry point; wires search/store/promote tools
│   ├── _plugin_scanner.py            # post_init: ChromaDB warmup + 30 s plugin rescan
│   └── __main__.py                   # `python3 -m telegram_interface`
├── registry/registry.py             # Lazy DI container; owns all service lifetimes
├── plugins/
│   ├── plugin_manager.py             # Hot-reload plugin orchestrator (registry-owned)
│   └── _loader.py                    # mtime-based directory reconcile
├── tools/
│   ├── goat_skills/                  # Hot-reload plugin tools: browse_page, fetch_content, shell_run, get_memory_metrics, get_recent_logs
│   ├── memory_tools.py               # search_memory tool
│   ├── memory_writer.py              # store_memory tool
│   └── memory_promote.py             # promote_memory tool
├── agents/                           # DAG agent pipeline — separate system, not on the memory turn path
├── mcp_server/                       # Optional standalone MCP introspection server
├── config/
│   ├── memory.toml                   # All memory tunables (AITS, prefetch, budget, cache, identity)
│   └── settings.py                   # LLM/Telegram env vars (read once at import)
├── conftest.py                       # pytest config
└── tests/                            # Faked-backend test suite (no external services)
```

`agents/` and `mcp_server/` ship in the repo but are not imported by the
orchestrator or Telegram bot — the live turn path is `telegram_interface` →
`orchestrator` → `memory.layers` → the three tiers.

---

## Configuration

All memory tunables are in `config/memory.toml`. **Redis and Letta connection
strings have no environment-variable override** — `memory/config.py` reads the
file with `tomllib.load` and never touches `os.environ`. Edit the file directly
for your setup.

```toml
[identity]
base_prompt = "You are GOAT, a helpful assistant with layered memory."

[working]
storage_url = "redis://localhost:6379/0"

[permanent]
letta_url = "http://localhost:8283"
l1_facts_max_tokens = 500        # cap on the Letta facts block

[aits]
budget_base = 2000
budget_confidence_multiplier = 4000
budget_complexity_max_bonus = 2000
budget_hard_cap = 12000

[prefetch]                        # the daemon — no confidence gate, timeout is the only blocker
timeout = 0.5                     # asyncio.wait_for bound; on exceed L3 is dropped (graceful)
max_results = 15
recency_window_days = 30
access_count_ref = 10
score_similarity_weight = 0.6     # blend: similarity + recency + access_count
score_recency_weight = 0.3
score_access_weight = 0.1

[retrieval_budget]
l2_context_cap = 8000
l2_floor_tokens = 500
l3_min_guarantee_tokens = 1200    # L3's guaranteed minimum slice (priority-inverted)
l3_gap_significance = 3.0        # max_gap/mean_gap threshold for the raw-result gap filter
max_results_per_search = 15

[session_cache]
ttl_seconds = 300                # L2.5 cache TTL
```

LLM provider and Telegram credentials are environment variables, read once by
`config/settings.py` at import time. See **[SETUP.md](SETUP.md)** for the full
list, install steps, how to run the bot, and what a successful startup looks
like.

### Known constraints

Two timing properties of the current code, both verifiable from the source:

- **`wait_for` cannot interrupt an in-flight `asyncio.to_thread` Chroma call.**
  On the rare query whose L3 search exceeds the 0.5 s bound, the turn blocks
  until the Chroma thread returns (observed ~0.5–0.8 s) rather than returning at
  exactly 0.5 s. Steady-state Chroma queries are ~0.2 s, so `wait_for` returns
  the result with no timeout and no blocking on the vast majority of turns.
- **The 0.5 s bound is tight for two-mechanism temporal queries.** A temporal
  query runs two embedding searches (thematic + temporal) ≈ 0.47 s warm, right
  at the edge; the first turn after process start pays a ~1 s cold start
  (ChromaDB warmup runs at `post_init`, but the first cached-path call is still
  lazy). Raise `timeout` in `[prefetch]` if prefetch reliability matters more
  than the bound.

---

## License

Apache 2.0 — see [LICENSE](LICENSE).