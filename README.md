# GOAT 2.0

**A personal AI agent with a memory that keeps the thread.** It lives in Telegram, and also in a Telegram Mini App
panel where you can watch it think. It remembers every conversation word for word, follows what a conversation is
about, and fetches what you will probably need next before you ask. Its context comes from three tiers: Redis,
ChromaDB and Letta.

---

## What GOAT's memory can do

- **It keeps working between messages.** After every reply, GOAT already gathers the context the next turn will
  probably need, with no time limit, while you read. Staying on a topic never means starting the search from
  scratch.
- **It follows the thread, not just the last sentence.** GOAT tracks what the conversation is about. It notices when
  you drift or switch subjects, and picks an earlier topic back up when you return to it. A short "da" or "ok" never
  breaks the thread.
- **Nothing is summarized away.** Every message and reply is archived verbatim, tagged with its time, topic and the
  names, places and dates it mentions. An exact detail from weeks ago can still be found.
- **Several ways of looking, one answer.** Meaning, exact wording, time expressions ("acum două ore", "pe 4 iulie")
  and the surrounding turns of the same conversation are searched together. A second model then re-reads the
  candidates and keeps what actually answers the question.
- **Background traffic keeps its own lane.** Reports from other agents and finished workflows are remembered too,
  but they never pull your own conversation off its topic.
- **One conversation, several doors.** With an admin chat configured, the Telegram chat and the panel's Chat page are
  the same conversation: the same orchestrator, the same memory, and one turn at a time.

---

## Quick start

```bash
git clone https://github.com/takashikiari/GOAT2-General-Orchestrated-Agent-Topology.git
cd GOAT2-General-Orchestrated-Agent-Topology
./run.sh          # Windows: run.bat
```

`run.sh` starts the setup wizard first (`setup/wizard.py`). Every step is a live check you can fix and retry, so no
file is edited by hand. It writes `goat2.toml`, `.env` (API keys, mode 0600, never commit it) and the memory config.
Then it installs the dependencies, runs the pre-flight checks (`setup/checks.py`) and starts the bot.

| Service | Required | Used for |
|---------|----------|----------|
| Telegram bot (via @BotFather) | yes | the chat interface |
| Redis | yes | L2 conversation, L2.5 thread state, caches |
| ChromaDB | yes | L3: every past conversation, searchable |
| Letta | optional | L1 permanent facts and the L0 identity override |

LLM providers offered by the wizard: **DeepSeek** (recommended), **OpenAI** and **Groq** (`setup/providers.toml`).

Details, environment variables and startup checks: **[SETUP.md](SETUP.md)**. Re-run the wizard any time with
`python3 setup/wizard.py --reconfigure`. Updates and rollbacks run with `setup/updater.py` / `setup/rollback.py`, or
`/update` / `/rollback` in Telegram.

---

## How a turn works

The per-turn driver is `Orchestrator.run` (`orchestrator/orchestrator.py`). It talks to memory through one façade,
`MemoryLayers` (`memory/layers.py`), and never imports a backend directly.

1. Read the thread state (L2.5) and embed the message, concurrently.
2. Classify the turn: **warm** (same thread), **drift** (the thread moved) or **cold** (a new topic). The
   classification is pure and cheap, with no I/O.
3. **Warm:** serve the context prepared after the previous turn. **Drift / cold:** run the retrieval pipeline for
   this message now, overlapped with the L0/L1/L2 fetch rather than added on top.
4. Assemble the context within an adaptive token budget, then call the LLM and its tools.
5. Save the turn: L2 immediately, the verbatim L3 archive in the background.
6. In the background, prepare the next turn's context and update the thread state. This step has no timeout.

---

## Architecture

| Layer | Content | Backed by |
|-------|---------|-----------|
| **L0** | Identity prompt: the Letta override if set, else `[identity] base_prompt` | Letta + config fallback |
| **L1** | Curated key → value facts, always in context (`l1_facts_max_tokens = 2000`) | Letta |
| **L2** | The current conversation (`[working] max_messages = 20`); older turns are promoted to L3 | Redis |
| **L2.5** | Thread state: topic centroid, held retrieval, topic id, archived topics | Redis |
| **L3** | Every past exchange, verbatim, plus entity metadata | ChromaDB (+ a separate chunk store) |

The three backends connect lazily and fail independently. A Letta outage empties L1 and falls back to the config
identity, and the turn continues. `ServiceRegistry` (`registry/registry.py`) is a lazy DI container that owns every
service lifetime.

### Retrieval (`memory/retrieval.py`)

- **Mechanisms, run concurrently:**
  - semantic search, global and scoped to the chat or topic;
  - BM25 (exact wording);
  - a temporal search when the message contains a time expression (`memory/temporal_route.py`, which parses the raw
    text with dateparser, Romanian included);
  - topic return (`topic_return_threshold = 0.75` against up to 10 archived topic centroids);
  - on drift turns, the previous turn's prepared context as a "prediction" candidate group.
- **Fusion:** Reciprocal Rank Fusion (`memory/result_merger.py`, `k = 60`). Each mechanism contributes `1/(60 + rank)`,
  and documents found by several mechanisms accumulate score. It is rank-based, so incompatible score scales never
  need tuning.
- **Then:**
  - entity boost (GLiNER, `urchade/gliner_multi-v2.1`);
  - near-empty entries dropped (`l3_min_alnum = 5`);
  - a cross-encoder rerank (`cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`, `top_k = 20`).
- **Chunk highlights:** candidates are found on whole entries. Each one is swapped, for the rerank only, with its
  closest ~220-token chunk, then expanded ±1 chunk.
- **Wave 2:** the top results pull in neighbouring turns of the same topic, within the L3 token budget. They never
  displace an original result.
- **Injection:** the context goes into the system prompt as `[Context recuperat din istoric]`, never as fake
  conversation turns.

### Thread state (L2.5, `memory/activation.py`)

| `cosine(message, thread centroid)` | State |
|-------------------------------------|-------|
| ≥ `drift_warm` = 0.80 | **warm**: serve the held context |
| between | **drift**: re-search; the centroid moves with weight `1/min(turn_count, 20)` |
| < `drift_cold` = 0.30 **and** lexical overlap < `lexical_low` = 0.10 | **cold**: new topic, or a return to an archived one |

- **Short messages:** a message with too few content words never forces a cold break.
- **Enriching writes:** when GOAT stores a fact on the current thread (`enriching_sim = 0.55`), the thread state is
  refreshed before the next turn reads it.
- **Expiry:** the state expires after `ttl_seconds = 3600` of silence. Expiry is cleanup, not a reset: the next turn
  simply starts cold.
- **Lanes:** each population on a chat keeps its own state: `human` (Telegram, panel), `channel` (agent-coord
  messages) and `dag` (workflow results). The keys are `activation:{chat_id}` and `activation:{chat_id}:{lane}`.

### AITS: adaptive token budget (`memory/aits.py`)

```
budget = 2000 + confidence × 4000 + complexity × 2000   (cap 12000)
```

A greeting gets about 2,000 tokens, a detailed multi-part question up to 12,000. L3 gets a guaranteed minimum slice
first (`l3_min_guarantee_tokens = 1200`), so a long conversation can never starve long-term memory to zero.

---

## Tools

The tool loop runs up to `max_iterations = 6`. Below the cap the model can chain tools; at the cap it must answer from
what it has. When a model writes tool calls as text (DSML markup), the calls are parsed and run as well.

**Memory tools:** `search_memory` (L3, with `after`/`before` filters), `store_memory` (L3), `promote_memory` (L1),
`set_identity` (L0), `read_l1`, `forget_fact` and `memory_status`.

**Hot-reload plugins** (`tools/goat_skills/`, rescanned every 30 s, no restart): `read_file`, `write_file`, `shell_run`,
`browse_page` (Playwright), `fetch_content` (crawl4ai), `get_memory_metrics`, `get_recent_logs`, `repo_git`
(read-only git over the machine's repositories) and the `agent_coord_*` tools. To add a tool, drop in a `.py` file
with `build(registry) -> list[ToolDefinition]`.

### Multi-agent workflows (`workflow/`, `agents/`)

For larger tasks GOAT can start a background DAG of specialist agents:

- **How the task is split:** GOAT itself splits the task, calling `start_workflow` with nodes and dependencies.
- **Roles:** planner, researcher, coder, critic, summarizer, tool_caller and memory.
- **Execution:** nodes without mutual dependencies run concurrently (`max_concurrent = 8`). Cycles are rejected up
  front (Kahn's algorithm), and each node has its own retries and timeout.
- **Talking to a running workflow:** GOAT can follow and steer it mid-run with `workflow_status`, `workflow_send` and
  `stop_workflow`, and keeps answering other messages meanwhile.

### Admin panel (`admin_panel/`)

A FastAPI backend and React frontend, served as a Telegram Mini App through a Cloudflare tunnel. Every API call is
authenticated with Telegram `initData`. Pages:

- **Chat**: the same conversation as Telegram, with live tool progress;
- **Dashboard** (metrics);
- **Memory**;
- **Conversations**;
- **Logs**;
- **Agents**: agent-coord messages.

---

## Configuration

`config/memory.toml` holds every memory tunable and is validated at startup (`memory/config_validator.py`). The LLM
and Telegram credentials live in `.env`, written by the wizard. Key values:

```toml
[working]
max_messages = 20

[permanent]
l1_facts_max_tokens = 2000

[retrieval_budget]
l3_min_guarantee_tokens = 1200

[aits]
budget_base = 2000
budget_confidence_multiplier = 4000
budget_complexity_max_bonus = 2000
budget_hard_cap = 12000

[prefetch]
max_results = 20

[activation]
ttl_seconds = 3600            # cleanup, not a reset
drift_warm = 0.80
drift_cold = 0.30
lexical_low = 0.10
enriching_sim = 0.55
topic_return_threshold = 0.75
topic_archive_max = 10

[reranker]
model = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
top_k = 20
```

---

## Project layout

```
memory/         layers.py (memory façade) · retrieval.py · result_merger.py (RRF) · activation.py (thread state, lanes)
                aits.py · context_assembler.py · temporal_route.py · gliner_extractor.py · enrichment.py
                working/ (Redis) · episodic/ (ChromaDB + chunks) · permanent/ (Letta)
orchestrator/   orchestrator.py (per-turn driver) · prefetch.py · activation_manager.py · chat_service.py
telegram_interface/   the bot, the agent-coord poller
admin_panel/    FastAPI backend + React Mini App (frontend/)
workflow/ agents/     the multi-agent DAG engine and its agents
tools/          memory tools, hot-reload plugins (goat_skills/), agent tools
setup/          wizard, pre-flight checks, updater, rollback
config/         memory.toml, settings
tests/          pytest suite: about 1,000 tests on faked backends, run in CI (python -m pytest tests/)
```

Release notes: [CHANGELOG.md](CHANGELOG.md).

---

## License

Apache 2.0. See [LICENSE](LICENSE).
