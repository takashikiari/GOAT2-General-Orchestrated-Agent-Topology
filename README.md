# GOAT 2.0

GOAT 2.0 is a Telegram-based AI agent built around a **proactive** layered
memory system. The core distinction from a standard RAG setup: memory retrieval
runs *before* the LLM responds, on every turn. Retrieval is held steady along a
topic thread — the brain keeps its recent activation rather than re-grounding
from a jittering slice every turn — and re-searches only when the thread shifts.
The model never has to ask "do I remember this?" because retrieval already
happened.

The per-turn driver is `Orchestrator.run` (`orchestrator/orchestrator.py:220`).
It talks to memory through one façade — `MemoryLayers` in `memory/layers.py` —
and never imports a physical backend directly.

---

## What makes it different

- **Proactive, not reactive.** The prefetch daemon (`Orchestrator._prefetch_daemon`,
  `orchestrator.py:403`) starts as the *first* step of every turn
  (`asyncio.create_task` at `orchestrator.py:253`) and runs in parallel with the
  L0/L1/L2 fetch. Retrieval precedes generation; it is not triggered by the model
  noticing a gap.
- **Brain activation, not a cache.** L2.5 holds per-chat *thread state* — the
  centroid of the current topic and the retrieval it produced. A follow-up on
  the same thread is served from the held activation (no search); the thread
  breaks only on a consensus shift (semantic drift AND lexical overlap both
  drop); an on-thread write folds into the activation in place. The LLM builds
  on a stable reality instead of a flickering one. See
  [L2.5 — the activation layer](#l25--the-activation-layer-brain-thread-state).
- **Three independent physical backends.** Redis (working + activation),
  ChromaDB (episodic), and Letta (permanent) each connect lazily and fail
  independently — a Letta outage empties L1 facts and the turn continues
  (`memory/layers.py:87`).
- **No static thresholds.** The L3 relevance filter is a ratio over the score
  distribution, scale-invariant by construction (`MemoryLayers._gap_filter`,
  `layers.py:387`). The context budget adapts per turn from two real signals
  (`memory/aits.py`). L3's minimum token slice is guaranteed *by construction*,
  not tuned (`memory/context_budget.py`). The activation thresholds *are*
  tuned — but to the measured embedding geometry, not guessed (see
  [Activation thresholds](#activation-thresholds)).
- **Complete fidelity.** Nothing is compressed or extracted. Every turn is
  archived verbatim (`_archive_turn`, `orchestrator.py:140`), so the full text of
  any past exchange is retrievable from a single semantic query — no summary
  stands between the model and the original words.

---

## Architecture

### Physical backends

| Backend | Class | Storage | Key / location |
|---------|-------|---------|----------------|
| **Working** | `WorkingMemory` (`memory/working/working.py`) | Redis | `goat2:working:{chat_id}` (messages), `cache:{chat_id}:{key}` (cold-path L2.5 cache), `activation:{chat_id}` (L2.5 thread state) |
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
| **L2.5 — Activation** | Per-chat thread state (centroid + held retrieval) | When the thread is active | Working / Redis |
| **L3 — Episodic** | Semantic long-term memory; injected when relevant | Conditional | Episodic / ChromaDB |

### The mapper (`memory/layers.py`)

`MemoryLayers` is the only memory interface the orchestrator and bot touch. It
maps the five logical layers onto the three physical tiers and exposes typed
methods: `assemble_context`, `get_working_context`, `save_working_context`,
`search_episodic`, `search_episodic_with_cache`, `store_episodic`,
`find_by_keys`, `bump_access`, `promote_fact`, the L2.5 cold-path cache API
(`get_cache`, `set_cache`, `invalidate_cache`, `clear_cache`, `cache_exists`),
and the L2.5 activation API (`get_activation`, `set_activation`, `clear_activation`,
`embed_query`). Neither `orchestrator.py` nor `telegram_interface/bot.py` imports
`WorkingMemory`, `EpisodicMemory`, or `PermanentMemory`.

### Service registry (`registry/registry.py`)

`ServiceRegistry` is a lazy DI container that owns every service lifetime —
LLM client, the three tiers, `MemoryLayers`, `MemoryAnalytics`, and
`PluginManager`. It is a class instance passed by the caller, **not** a
module-level singleton (the zero-singleton rule). Each backend is built on
first property access.

---

## The prefetch daemon

This is the core differentiator. It runs on every turn, before the LLM call, and
branches on the turn's relation to the per-chat activation: **cold** searches,
**warm** serves the held activation, **drift** does a targeted refresh.

### 1. Turn state — cold / warm / drift

Before creating the prefetch task, `run()` fetches the chat's activation and a
query embedding concurrently with the L0/L1/L2 fetch
(`orchestrator.py:246-249`), then classifies the turn
(`classify_turn` at `orchestrator.py:252`, logic in `memory/activation.py:192`):

| State | Trigger | What the daemon does |
|-------|---------|----------------------|
| **cold** | No prior activation, embedding unavailable, or a **consensus shift** (semantic drift < `drift_cold` AND lexical overlap < `lexical_low`) | Full three-mechanism search; build a fresh activation |
| **warm** | `cosine(query, centroid) ≥ drift_warm` — same thread | Serve the held activation re-scored by recency; **skip all search** |
| **drift** | The middle band — moved but not shifted | Targeted single-mechanism (thematic) refresh against the new query |

A single jittery signal cannot reset the thread: the consensus-shift rule needs
*both* the embedding to drift and the lexical overlap to drop. A short "tell me
more" that drifts in embedding space but keeps the same words stays warm/drift,
not cold.

### 2. Cold — three mechanisms, evaluated independently, no gate

The daemon classifies the query three ways and runs every mechanism that scores
above zero. There is **no confidence gate** on whether prefetch runs at all —
the timeout is the only blocker. Classification lives in
`memory/query_classifier.py`:

| Mechanism | Signal | Source | Retrieval |
|-----------|--------|--------|-----------|
| **Temporal** | A completed-past date range | `extract_temporal_range` (`memory/temporal_parser.py`, `dateparser`, `STRICT_PARSING`) | `search_episodic(after, before)` — filtered semantic search |
| **Thematic** | Always 1.0 | none — unconditional | `search_episodic_with_cache` — cold-path cached semantic search (carries `cache_key`) |
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
`asyncio.gather(*tasks, return_exceptions=True)` (`orchestrator.py:485`); a
single mechanism that raises is skipped (logged with its `mechanism=` tag —
`thematic` / `temporal` / `specific_key` — at `orchestrator.py:495`, so a
ChromaDB `Error finding id` desync self-identifies), the rest still contribute.

### 3. Warm — serve the activation, skip all search

When the query sits on the same thread, the daemon serves
`rescore_recency(activation.merged, now)` (`orchestrator.py:438`) — the held
results re-scored against the current time so older facts attenuate. **No
ChromaDB query runs.** The centroid is held steady (a short follow-up must not
move the thread); only the recent-queries window is extended for the lexical
consensus signal. This is the coherence payoff: the second turn of a thread
builds on the exact same reality as the first, with zero retrieval cost.

### 4. Drift — targeted single-mechanism refresh

When the query moved but not enough to be a shift, the daemon re-runs **only
the thematic mechanism** uncached (`search_episodic`, `orchestrator.py:452`) and
replaces the activation's results around the new query — the centroid moves to
the new query embedding. The temporal and specific-key mechanisms are skipped:
on a moved-but-not-shifted thread, only thematic can surface new associations.

### 5. Merge and score

`merge_results` (`memory/result_merger.py:71`) dedupes across the result groups
by `message_id`, scores each result, and sorts best-first. The blend is fixed
by config weights (`[prefetch]`):

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
`last_accessed_ts` bumped fire-and-forget (`orchestrator.py:515`).

### 6. Bounded wait, graceful degradation

The daemon is awaited under
`asyncio.wait_for(prefetch_task, timeout=PREFETCH_TIMEOUT)` (0.5 s,
`orchestrator.py:279`). On timeout or any exception the task is cancelled and
the turn continues without L3 (`orchestrator.py:286`) — the on-demand
`search_memory` tool is the fallback. A successful run returns
`(results, cache_hit, cache_key)`; the orchestrator then persists/refreshes the
activation via `_update_activation` (`orchestrator.py:298`).

### 7. Injection — system prompt, not conversation history

Results are passed to `assemble_context`, which fits them into the L3 token
slice and emits them as a `[Context recuperat din istoric]` block
(`layers.py:323`). Because the daemon pre-scored the results, `assemble_context`
detects the `blended_score` field and sorts best-first *instead of* running the
gap filter (`layers.py:314-316`). The block is joined into `system_content`
(`orchestrator.py:314`) — part of the system prompt. The conversation history
sent to the API is only `[{system}, {user}]` (`orchestrator.py:323-325`); L3 never
appears as fake prior turns.

### 8. Relevance for non-daemon results — the gap filter

When `assemble_context` receives **raw** results with no `blended_score` (the
on-demand `search_memory` path, or direct tests), it falls back to
`MemoryLayers._gap_filter` (`layers.py:387`):

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

## L2.5 — the activation layer (brain thread state)

L2.5 is not a cache. It is a per-chat **activation** — the brain's held mental
model of the current topic — stored as one JSON blob per chat in Redis under
`activation:{chat_id}` (a 7-day cleanup TTL that is **not a reset**: expiry just
means "re-derive cold next time"; topic continuity is semantic, not time-based).

The activation (`memory/activation.py:58`) holds:

- `centroid` — the thread's query embedding. Set on cold/drift, held steady on
  warm so short follow-ups can't move it.
- `merged` — the cold turn's final merged+scored L3 results; served re-scored by
  recency on warm turns.
- `last_query` — the substantive query that produced `merged`; the enriching
  refresh re-searches against this.
- `recent_queries` — a rolling window (capped at `lexical_window`) for the
  lexical-overlap consensus signal.

### Embeddings — free, in-vector-space-with-retrieval

`EpisodicMemory.embed_query` (`memory/episodic/episodic.py:104`) reuses the
collection's own embedding function (chromadb's bundled ONNX MiniLM) — the same
model the semantic search uses — so the thread centroid lives in the same
vector space as retrieval at **no new dependency and no per-turn API call**.
`_embedding_function` is a ChromaDB internal, so the helper degrades to `None`
on any failure (missing attribute, version change, model not initialised) and
the turn falls back to cold — an embedding failure never breaks a turn. Embeddings
are coerced to native `float` so the activation serialises to Redis cleanly.

### Enriching vs filing writes — the ordering invariant

When GOAT stores a fact via the `store_memory` tool, the orchestrator classifies
the write against the current thread's centroid (`classify_write`,
`memory/activation.py:217`):

- **enriching** — `cosine(write_content, centroid) ≥ enriching_sim`: the content
  belongs to the current thread. The activation is refreshed **in place,
  synchronously before `run()` returns** (`_enriching_refresh`,
  `orchestrator.py:555`): it re-searches the thread's `last_query` (uncached, so
  the just-written fact is now retrievable) and replaces the activation's
  results. The next turn sees the new learning folded in — a brain files new
  learning in before it speaks again.
- **filing** — off-thread: stored in L3, the current activation left untouched.
  It surfaces when a *future* thread about that topic activates.

Only GOAT's organic `store_memory` calls are classified; the automatic
`_archive_turn` write bypasses the tool and never triggers a refresh.

### Time attenuates, never resets

Warm turns serve `rescore_recency(activation.merged, now)`
(`memory/activation.py:231`), which re-blends the held results against the current
time using the *same* recency term the merge uses. Older held facts drop in
score across a long thread with no separate attenuation code and no search — the
"time attenuates, never resets" property. No time-based reset ever runs.

### Activation thresholds

The thresholds are **tuned to the measured cosine geometry of the bundled MiniLM
L6 v2**, not guessed (verified via `scripts/threshold_sanity.py`, 2026-07-03):

| cosine(query, centroid) | Measured band | State |
|--------------------------|---------------|-------|
| 0.80 – 1.00 | paraphrase + intent follow-up (same thread) | **warm** |
| 0.55 – 0.80 | related / same-entity-different-facet | **drift** |
| < 0.55 | different target (+ low lexical overlap) | **cold** |

So `drift_warm = 0.80`, `drift_cold = 0.55`, `enriching_sim = 0.55`,
`lexical_low = 0.15`. The first-cut values (0.92 / 0.70) were ~0.12 too high: they
pushed genuine follow-ups (cosine ≈ 0.81) into drift, so the activation never
held steady and re-searched every turn — defeating the whole point.

**`scripts/threshold_sanity.py` is the gate.** Re-run it (needs `source .env`; no
LLM cost — just embeddings) whenever the thresholds or the embedding model
change. It embeds query pairs that *should* land in each band and checks they do.
It also runs `scripts/enriching_check.py`, a no-LLM live check that the
enriching-write refresh folds a just-written fact into the activation before the
next turn (the ordering invariant), against real Redis + ChromaDB.

**Known limitation (flagged, not fixed):** the consensus-shift rule rarely fires
for "what is my X → what is my Y" questions, because function words (`what`, `is`,
`my`) keep lexical overlap ~0.4–0.7 even across different targets — so a true topic
change often lands in **drift**, which still re-runs thematic search (correct
answer, just not logged as a `thread_break`). Acceptable; revisit if analytics
show drift-rate suspiciously high.

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
`assemble_context` (`layers.py:321`); `l3_used` is how many results fit.

---

## Memory mechanics

### Write-through archive (`_archive_turn`, `orchestrator.py:140`)

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
(`layers.py:142-143`) so the merge-score terms exist from the first retrieval.

### L2 conversation trim (`MemoryLayers._trim_recent_messages`, `layers.py:327`)

When L2 history exceeds its cap, messages are dropped oldest-first with one
exception: the very first message (the topic-setter) is pinned provided it is
small (< 25% of the cap). This prevents a long session from losing all topic
context while still prioritising recent messages.

---

## Tool-calling flow

### Agentic loop (`_run_tool_round`, `orchestrator.py:583`)

The tool round is an agentic loop — the same shape Claude Code runs. Each
iteration executes the model's pending `tool_calls`, folds the assistant
`tool_calls` + tool results into the running conversation, and calls the model
again. Below `AGENTIC_MAX_ITERATIONS` (`config/memory.toml [tool_loop]`,
default 6) it is called WITH tools (`orchestrator.py:661`) so the model can
chain tools across the turn — read → search → write → verify → synthesise —
in whatever order the query needs; a plain-text reply (no `tool_calls`) is
natural termination. At the cap it is called WITHOUT tools
(`orchestrator.py:672`) so a stuck model must synthesise from what it has
gathered. The cap is a HARD backstop, not a grounding decider: it never inspects
content or withdraws a claim (that was the harmful regex-detector path, now
removed). It only bounds cost/latency per turn — worst case
`1 + AGENTIC_MAX_ITERATIONS` LLM calls (the +1 is the initial decision call in
`run()`) — and prevents a runaway loop from hanging a single Telegram turn. A
turn that uses no tools never enters the loop and stays a single LLM call.

### Synthesis bridge (`_TOOL_SYNTHESIS_BRIDGE`, `orchestrator.py:94`)

After executing each batch of tool calls, a fixed user turn is appended before
the next LLM call (every iteration — both "continue with tools" and the
cap-forced synthesis):

> "Respond to my request based on the tool results above. Only state specific
> numbers, counts, or names if they appear verbatim in the tool output — if a
> figure was not returned by the tools, say so explicitly rather than guessing."

This is in-context support, not a decision: offered tools, the model may still
call another tool instead of answering. It also prevents raw tool-output
formatting from dominating generation style (a context-position proximity effect
the distant system-message persona cannot overcome).

### Grounding — in-context, by the orchestrator (no post-hoc mechanism)

There is no separate verifier and no corrective LLM call after synthesis. The
model grounds itself in the synthesis call: it has the tool output in context,
and `_TOOL_SYNTHESIS_BRIDGE` (`orchestrator.py:94`) tells it to state only
figures that appear verbatim in the tool results and to say so explicitly
rather than guess. Grounding is the LLM's job *because only it has the context*
— the query, the conversation history, and the tool output. A mechanism that
sees only text fragments cannot judge groundedness: it would flag real numbers
drawn from history as ungrounded, and a corrective call would then force the
model to withdraw true facts. Mechanisms here are support only — the cap bounds
the loop, the bridge nudges each generation, the `[Tool calls]` evidence block
(below) gives future turns context — and the orchestrator stays decisional.

### DSML fallback (`_run_dsml_tool_round`, `orchestrator.py:697`)

`deepseek-v4-flash` sometimes returns tool-call intent as DSML markup in the
message content instead of the standard `tool_calls` field. The orchestrator
detects this with a regex, parses the invocations, executes them, and returns
raw tool output directly — no second LLM call on this path. This is a separate
fallback, not part of the agentic loop.

### Tool evidence in L2 (`_compact_tool_summary`, `orchestrator.py:119`)

When tools were called, the saved assistant message in L2 is prefixed with a
compact evidence record, one line per call:
`called {name}({args_preview}) → {result_preview}` — accumulated across **every
iteration** of the loop. Large-output tools (`browse_page`, `fetch_content`)
store only `[N chars]`; `shell_run` stores head + tail. This gives future turns
verifiable grounding for past tool use, structurally distinct from a narrated
claim.

---

## Memory tools

Three core tools are wired by the Telegram bot at startup (`bot.py:47-50`):

| Tool | Layer | When GOAT uses it |
|------|-------|-------------------|
| `search_memory` (`tools/memory_tools.py`) | L3 read | User references something not in the current conversation; supports `after`/`before` ISO-8601 filters |
| `store_memory` (`tools/memory_writer.py`) | L3 write | User shares something worth keeping across sessions (may trigger an enriching refresh) |
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
reads `registry.plugin_manager.tools` (`orchestrator.py:214`); `scan()`
atomically swaps the tool list, and a plugin that fails to build is skipped
while its last-known-good tools are kept — a broken edit never wipes a working
tool.

Bundled plugins: `browse_page` (Playwright), `fetch_content` (crawl4ai),
`shell_run` (subprocess), `read_file` (bounded host file read), `write_file`
(bounded host file write/append), `get_memory_metrics`, `get_recent_logs`.
Drop a `.py` file exposing `build(registry) -> list[ToolDefinition]`
into `tools/goat_skills/` to add a tool without a restart.

---

## Observability

One `MemoryObservation` (`memory/observability.py`) is emitted as a JSON log
line per turn, built by `ObservationCollector` (`memory/observability_collector.py`)
and aggregated by the registry-owned `MemoryAnalytics`
(`memory/analytics.py`). Fields include: AITS confidence/complexity/budget,
L2.5 cache hit/miss + key, **activation state** (`cold` / `warm` / `drift`),
`thread_break`, `write_kind` (`enriching` / `filing` / `none`),
`enriching_refresh`, latency per stage (`classify` / `search` / `assemble`
/ `inject` / `llm` / `save` / `total`), tokens per tier (L0+L1 / L2 / L3),
prefetch outcome (attempted / succeeded / timeout / blocks injected / blocks
used), and results found/used. `MemoryAnalytics` aggregates
`activation_*_rate`, `thread_breaks`, `enriching_writes`, `filing_writes`, and
`enriching_refreshes`. A summary report is logged every
`ANALYTICS_LOG_INTERVAL` requests (default 100).

The `intent_category` field is a coarse, analytics-only label derived from the
confidence tier (`observability_collector.py:128`): `recall` (≥ 0.4),
`greeting` (< 0.3), else `conversational`. It labels the analytics tier and never
gates prefetch.

---

## Project layout

```
goat2/
├── memory/
│   ├── layers.py                    # Backend mapper — the only memory interface (L0-L3 + L2.5)
│   ├── activation.py                # L2.5 activation layer: store + turn/write classification (pure logic)
│   ├── aits.py                      # Adaptive Intent Token Scaling (confidence + complexity)
│   ├── context_budget.py            # Priority-inverted L2/L3 budget split
│   ├── result_merger.py             # Prefetch merge: dedupe + blended score (0.6/0.3/0.1)
│   ├── query_classifier.py          # 3-mechanism prefetch classification (temporal/thematic/specific-key)
│   ├── temporal_parser.py           # dateparser completed-past range extraction
│   ├── session_cache.py             # L2.5 cold-path TTL cache (Redis) — used by search_episodic_with_cache
│   ├── promote.py                   # L3 → L1 promotion, cap-guarded
│   ├── budget.py                    # Token estimation + result-count enforcement
│   ├── observability.py             # MemoryObservation dataclass (per-turn JSON, incl. activation state)
│   ├── observability_collector.py   # Per-turn observation builder + intent category + set_activation
│   ├── analytics.py                 # Registry-owned metrics aggregator (incl. activation counters) + report
│   ├── config.py                    # Reads config/memory.toml; all numeric knobs
│   ├── working/working.py           # Redis-backed working memory (L2 + activation store)
│   ├── episodic/
│   │   ├── episodic.py              # ChromaDB lifecycle + store/search + embed_query (L3)
│   │   ├── queries.py               # find_by_keys, bump_access, get_recent/count/delete (L3 mixin)
│   │   └── warmup.py                 # Collection pre-warm at startup
│   └── permanent/permanent.py        # Letta-backed permanent memory (L0/L1)
├── orchestrator/
│   ├── orchestrator.py               # Per-turn driver: classify turn → prefetch → AITS → assemble → LLM → tool round → enriching refresh → save+archive
│   └── tools.py                      # ToolDefinition type
├── scripts/
│   ├── threshold_sanity.py           # Embedding gate: query pairs must land in their warm/drift/cold band
│   ├── enriching_check.py            # No-LLM live check: enriching write folds into activation before next turn
│   └── repair_episodic.py            # Detect + rebuild a ChromaDB HNSW/metadata desync ("Error finding id")
├── telegram_interface/
│   ├── bot.py                        # Telegram entry point; wires search/store/promote tools
│   ├── _plugin_scanner.py            # post_init: ChromaDB warmup + 30 s plugin rescan
│   └── __main__.py                   # `python3 -m telegram_interface`
├── registry/registry.py             # Lazy DI container; owns all service lifetimes
├── plugins/
│   ├── plugin_manager.py             # Hot-reload plugin orchestrator (registry-owned)
│   └── _loader.py                    # mtime-based directory reconcile
├── tools/
│   ├── goat_skills/                  # Hot-reload plugin tools: browse_page, fetch_content, shell_run, read_file, write_file, get_memory_metrics, get_recent_logs
│   ├── memory_tools.py               # search_memory tool
│   ├── memory_writer.py              # store_memory tool
│   └── memory_promote.py             # promote_memory tool
├── agents/                           # DAG agent pipeline — separate system, not on the memory turn path
├── mcp_server/                       # Optional standalone MCP introspection server
├── config/
│   ├── memory.toml                   # All memory tunables (AITS, prefetch, budget, cache, activation, identity)
│   └── settings.py                   # LLM/Telegram env vars (read once at import)
├── conftest.py                       # pytest config
└── tests/                            # Faked-backend test suite (no external services) + test_activation (pure logic)
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
max_results = 20
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
max_results_per_search = 20

[session_cache]
ttl_seconds = 300                # cold-path L2.5 cache TTL (search_episodic_with_cache)

[activation]                      # L2.5 brain-activation layer — see memory/activation.py
ttl_seconds = 604800             # 7-day cleanup horizon — NOT a reset; expiry just re-derives cold
drift_warm = 0.80                # cosine(query, centroid) >= this → warm (serve activation, skip search)
drift_cold = 0.55                # cosine < this AND lexical_overlap < lexical_low → consensus shift (cold)
lexical_low = 0.15               # lexical-overlap floor for the consensus-shift rule (both must drop)
enriching_sim = 0.55             # cosine(write_content, centroid) >= this → enriching (refresh in place)
lexical_window = 5               # recent queries kept per thread for the lexical-overlap signal
```

LLM provider and Telegram credentials are environment variables, read once by
`config/settings.py` at import time. See **[SETUP.md](SETUP.md)** for the full
list, install steps, how to run the bot, and what a successful startup looks
like.

### Known constraints

Two timing properties of the current code, both verifiable from the source:

- **`wait_for` cannot interrupt an in-flight `asyncio.to_thread` Chroma call.**
  On the rare cold query whose L3 search exceeds the 0.5 s bound, the turn
  blocks until the Chroma thread returns (observed ~0.5–0.8 s) rather than
  returning at exactly 0.5 s. Steady-state Chroma queries are ~0.2 s and warm
  turns skip Chroma entirely, so `wait_for` returns with no timeout and no
  blocking on the vast majority of turns.
- **The 0.5 s bound is tight for two-mechanism cold queries.** A temporal cold
  query runs two embedding searches (thematic + temporal) ≈ 0.47 s warm, right
  at the edge; the first turn after process start pays a ~1 s cold start
  (ChromaDB warmup runs at `post_init`, but the first cached-path call is still
  lazy). Raise `timeout` in `[prefetch]` if prefetch reliability matters more
  than the bound.

---

## Verification

Three no-LLM live scripts (run after `source .env`; they use only Redis +
ChromaDB, no provider calls):

- `python3 -m scripts.threshold_sanity` — embeds query pairs that *should* land
  in each band and checks they do. The gate the activation thresholds were tuned
  against; re-run whenever thresholds or the embedding model change.
- `python3 -m scripts.enriching_check` — proves the enriching-write ordering
  invariant: an on-thread write folds into the activation in place before the
  next turn reads it.
- `python3 -m scripts.repair_episodic` — checks the ChromaDB collection for the
  HNSW/metadata desync behind a cold-turn `prefetch mechanism raised …
  Error finding id` (check-only by default; `--rebuild` exports → backs up →
  drops → recreates → re-adds the rows verbatim, then re-probes).

The faked-backend test suite (`python3 -m pytest -q`) covers the pure activation
logic (`tests/test_activation.py` — cosine, lexical overlap, turn/write
classification, recency rescoring) and the orchestrator memory flow; the fakes
return empty activations + `None` embeddings so every single-turn test sees a
cold turn and the pre-activation behaviour is preserved.

---

## Benchmark results

The benchmark suite (`python3 -m benchmark`) runs against a live orchestrator
with real Redis + ChromaDB. Each case preloads a conversation, asks a query,
and scores the response with fuzzy match + semantic similarity. `episodic_only`
cases are preloaded into L3 only (bypassing L2), forcing the full prefetch path.
A **grounding check** re-queries L3 after scoring to verify the answer was
actually retrieved — a correct-but-ungrounded answer is flagged as a guess.

### Full suite — 41 cases across 11 datasets

| Dataset | Cases | Accuracy | Grounded | Ungrounded | Avg latency |
|---|---|---|---|---|---|
| memory_recall | 10 | **100%** | 100% | 0 | 1.8 s |
| temporal | 5 | **100%** | 100% | 0 | 1.8 s |
| multi_turn | 3 | **100%** | 100% | 0 | 1.9 s |
| cache | 4 | **100%** | 100% | 0 | 1.7 s |
| prefetch | 4 | **100%** | 100% | 0 | 1.5 s |
| multi_hop | 3 | **100%** | 100% | 0 | 1.8 s |
| distractor (8 × L2) | 3 | 66.7% | 100% | 0 | 2.3 s |
| distractor_15 | 3 | **100%** | 100% | 0 | 1.6 s |
| distractor_20 | 3 | 66.7% | 50% | 1 | 3.1 s |
| distractor_25 | 3 | **100%** | 100% | 0 | 2.0 s |
| distractor_30 | 3 | **100%** | 100% | 0 | 1.9 s |

### Distractor stress test — L3 retrieval under high noise

All cases use `episodic_only=True` (L3-only), multi-sentence paragraphs,
non-guessable arbitrary answers, and 3–4 lexical decoys placed in the same
semantic domain as the query. At each tier the target fact is buried at a
randomised mid-conversation position.

| Dataset | Distractors | L3 entries | Accuracy | Grounded | Ungrounded | Avg latency |
|---|---|---|---|---|---|---|
| distractor_15 | 15 | 30 | **100%** | 100% | 0 | 1.6 s |
| distractor_20 | 20 | 40 | 66.7% | 50% | **1** | 3.1 s |
| distractor_25 | 25 | 50 | **100%** | 100% | 0 | 2.0 s |
| distractor_30 | 30 | 60 | **100%** | 100% | 0 | 1.9 s |
| distractor_50 | 50 | 100 | 66.7% | 100% | 0 | 3.7 s |
| distractor_100 | 100 | 200 | 66.7% | 100% | 0 | 3.5 s |
| distractor_200 | 200 | 400 | **100%** | 100% | 0 | 2.9 s |
| distractor_400 | 400 | 800 | **100%** | 100% | 0 | 3.9 s |
| distractor_800 | 800 | 1 600 | 66.7% | 100% | 0 | 3.1 s |

**Key findings:**

- **Grounding stays 100% on d25–d800.** Even when the system cannot retrieve
  the fact, it does not guess — it reports that it does not know. The one
  ungrounded correct result (d20-03, name "Diana") is a data quality issue:
  the answer is a common name guessable without retrieval.
- **Failures are ranking failures, not volume failures.** The system handles
  d200 and d400 perfectly. Failures at d50, d100, and d800 occur when several
  lexical decoys share the exact semantic sub-domain of the target query
  (e.g. "firmware PIN" queries competing with "alarm PIN", "locker PIN",
  "building entry PIN"), pushing the target below the top-20 cutoff.
- **Volume alone is not the problem.** d400 (800 L3 entries) succeeds at 100%;
  d800 (1 600 entries) at 66.7%. The determining factor is per-case domain
  density of the decoys, not the raw corpus size.
- **No degradation curve.** Accuracy does not decrease monotonically with N.
  This confirms the HNSW index and blended-score ranking scale correctly;
  the limit is semantic, not computational.

The adversarial benchmark serves as a worst-case bound. In production, a
conversation of comparable volume would span many unrelated domains; the
dense same-domain competition present in these cases does not arise naturally.

---

## License

Apache 2.0 — see [LICENSE](LICENSE).