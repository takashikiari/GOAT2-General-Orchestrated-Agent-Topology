# GOAT 2.0 — Setup & Usage

## Quick start

```bash
git clone https://github.com/takashikiari/GOAT2-General-Orchestrated-Agent-Topology.git
cd GOAT2-General-Orchestrated-Agent-Topology
./run.sh          # Windows: run.bat
```

The first launch detects a missing `goat2.toml` or `.env` and starts the interactive setup wizard automatically. The wizard guides you through provider selection, API keys, and optional services, then writes `goat2.toml` and `.env`. See [README.md § Setup](README.md#setup) for the full wizard reference.

---

## Prerequisites

| Requirement | Minimum version | Notes |
|-------------|----------------|-------|
| Python | 3.11 | `tomllib` is stdlib from 3.11 |
| Redis | any recent | L2 — current conversation + L2.5 activation state |
| ChromaDB | any recent | L3 — long-term episodic memory (all past conversations) |
| Letta server | — | L1 — permanent facts, preferences, and knowledge; if unreachable, L1 degrades to `{}` but the bot still runs |

> **Important — no env vars for service URLs.**
> Redis and Letta connection details are read exclusively from `config/memory.toml`
> (`memory/config.py` uses `tomllib.load()` with no `os.environ` call).
> There is no environment variable override path for either.
> If your Redis or Letta instance runs on a non-default host/port, you must edit
> `config/memory.toml` directly — setting these in `.env` has no effect.

---

## Install

```bash
pip install -r requirements.txt
```

For the optional `fetch_content` and `browse_page` plugin tools:

```bash
pip install crawl4ai playwright
playwright install chromium
```

---

## Environment variables

All read by `config/settings.py` at import time. Nothing else reads `os.environ` directly.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `<PROVIDER>_API_KEY` | **Yes** | — | API key for the chosen provider — e.g. `DEEPSEEK_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY`, `GEMINI_API_KEY`. Ollama needs no key. |
| `TELEGRAM_BOT_TOKEN` | **Yes** | — | Bot token from @BotFather |
| `MODEL_NAME` | No | `deepseek-v4-flash` | Model identifier passed to the API |
| `BASE_URL` | No | `https://api.deepseek.com` | OpenAI-compatible provider base URL |
| `TEMPERATURE` | No | `0.5` | Sampling temperature (0–2) |
| `MAX_TOKENS` | No | `2048` | Max tokens per LLM response |
| `TIMEOUT_SECONDS` | No | `30.0` | HTTP timeout for LLM calls |
| `GOAT_LOG_DIR` | No | `/tmp/goat2/logs` | Directory for the rotating log file |

The setup wizard writes these automatically to `.env`. Minimum `.env` for DeepSeek (the recommended default):

```bash
export DEEPSEEK_API_KEY=sk-...
export TELEGRAM_BOT_TOKEN=123456:ABC...
```

---

## Services

**Redis** — must be reachable before the bot starts. Edit `config/memory.toml` to point at your instance:

```toml
[working]
storage_url = "redis://localhost:6379/0"   # change host/port/db here
```

Start a local instance with:

```bash
redis-server
```

**Letta** — required for L1 permanent memory (facts, preferences, knowledge promoted across sessions) and the optional L0 identity override (`set_identity` tool). L0 always loads from `[identity] base_prompt` in `memory.toml` as a fallback; if a Letta identity override is set it takes precedence. Edit `config/memory.toml` to point at your instance:

```toml
[permanent]
letta_url = "http://localhost:8283"   # change host/port here
```

If Letta is unreachable at startup, L1 returns an empty facts dict, the L0 identity falls back to `[identity] base_prompt` in `memory.toml`, and the bot continues with a warning per turn. The `set_identity` tool will return an error if Letta is down, but no turn crashes.

---

## Configuration

`config/memory.toml` is the **only** place to configure service connection strings — there are no env var equivalents. Edit this file for your local setup before running the bot.

```toml
[identity]
base_prompt = "You are GOAT, a helpful assistant with layered memory."

[working]
storage_url = "redis://localhost:6379/0"   # your Redis URL

[permanent]
letta_url = "http://localhost:8283"        # your Letta server URL

[aits]
budget_base = 2000
budget_hard_cap = 12000

[prefetch]
timeout = 1.0                     # asyncio.wait_for bound; graceful degradation on exceed

[retrieval_budget]
l3_min_guarantee_tokens = 1200
l3_gap_significance = 3.0

[activation]
topic_return_threshold = 0.75    # cosine sim to resume an archived topic on cold break
topic_archive_max = 10           # past topic centroids kept per chat

[session_cache]
ttl_seconds = 300
```

---

## Run the bot

```bash
./run.sh          # recommended — runs pre-flight checks then starts the bot
```

Or directly:

```bash
python3 -m telegram_interface
```

---

## What a successful startup looks like

```
INFO  Starting GOAT 2.0 Telegram bot (model=deepseek-v4-flash)
INFO  Telegram bot application built (model=deepseek-v4-flash)
INFO  EpisodicMemory warmed up (collection=episodic_memory)
```

The `EpisodicMemory warmed up` line confirms ChromaDB connected and the local ONNX embedding model loaded. The bot is ready to receive messages after that line appears.

If Letta is unreachable, you will see a warning before each turn but no crash:

```
WARNING  PermanentMemory unavailable, L1 facts empty: ...
```

---

## Run tests

```bash
python3 -m pytest tests/ -v
```

Tests use no external services — all backends are faked. The suite should complete in under two seconds.

---

## Benchmark suite

The benchmark suite runs against a **live** orchestrator with real Redis, ChromaDB and the configured LLM provider. Redis and ChromaDB must be running and the LLM API key must be set before you start.

### List available datasets

```bash
python3 -m benchmark --list
```

### Run a single dataset

```bash
python3 -m benchmark --dataset memory_recall
python3 -m benchmark --dataset distractor_30 --verbose   # per-case log lines
```

### Run all datasets

```bash
python3 -m benchmark --all
python3 -m benchmark --all --verbose
```

### Save results to disk

```bash
python3 -m benchmark --all --output results.json --csv results.csv
```

### Available datasets

| Dataset | Cases | What it tests |
|---------|-------|--------------|
| `memory_recall` | 10 | Single-fact recall from L2 |
| `temporal` | 5 | Facts anchored to a time reference |
| `multi_turn` | 3 | Fact buried mid-thread |
| `cache` | 4 | L2.5 search cache hit on repeated query |
| `prefetch` | 4 | L3-only retrieval via prefetch daemon |
| `multi_hop` | 3 | Two facts combined to answer |
| `distractor` | 3 | Target among ~8 distractors in L2 |
| `distractor_15` | 3 | 15 distractors, L3-only, `episodic_only` |
| `distractor_20` | 3 | 20 distractors, L3-only, `episodic_only` |
| `distractor_25` | 3 | 25 distractors, multi-sentence, lexical decoys |
| `distractor_30` | 3 | 30 distractors, multi-sentence, lexical decoys |
| `distractor_50` | 3 | 50 distractors, programmatic generation |
| `distractor_100` | 3 | 100 distractors |
| `distractor_200` | 3 | 200 distractors |
| `distractor_400` | 3 | 400 distractors |
| `distractor_800` | 3 | 800 distractors — preloading takes ~8 min total |

> **Note on `distractor_400` and `distractor_800`:** preloading writes 800 and 1 600 messages to ChromaDB respectively. This takes several minutes per case. The LLM query itself remains fast (~2–5 s); only the setup is slow. Use `--dataset distractor_400` / `--dataset distractor_800` separately if you want to run just those tiers.

### LLM judge (optional)

Pass `--judge` to override lexical scoring with an LLM verdict on ambiguous cases:

```bash
python3 -m benchmark --dataset multi_hop --judge
```

### What the report shows

```
📊 BENCHMARK REPORT
   Dataset: distractor_30
   Total tests: 3
   Correct: 3
   Accuracy: 100.0%
   Avg latency: 1.9s
   Cache hit rate: 0.0%
   Prefetch usefulness: 100.0%
   Grounded correct: 3 (fidelity 100%)
   Ungrounded correct (guessed): 0
```

- **Accuracy** — fraction of cases where the response matches the expected answer (fuzzy match by default)
- **Grounded correct** — correct answers that were actually retrieved from L3 (verified by re-querying ChromaDB after scoring)
- **Ungrounded correct** — correct answers where the fact was *not* in the retrieved results — the model guessed. This is the dangerous case: right answer, wrong source
- **Prefetch usefulness** — fraction of turns where the prefetch daemon returned at least one result

---

## MCP server (optional)

An MCP server exposing memory introspection tools is included at `mcp_server/`. Run it with:

```bash
python3 -m mcp_server
```

This is independent of the Telegram bot and not required for normal operation.
