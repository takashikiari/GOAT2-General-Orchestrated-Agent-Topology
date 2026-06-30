# GOAT 2.0 — Setup & Usage

## Prerequisites

| Requirement | Minimum version | Notes |
|-------------|----------------|-------|
| Python | 3.12 | `tomllib` (stdlib from 3.11) is used by `memory/config.py` |
| Redis | any recent | L2 working memory + L2.5 session cache |
| Letta server | — | L0/L1 permanent memory; if unreachable, L1 facts degrade to `{}` but the bot still runs |

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
| `DEEPSEEK_API_KEY` | **Yes** | — | API key for the LLM provider |
| `TELEGRAM_BOT_TOKEN` | **Yes** | — | Bot token from @BotFather |
| `MODEL_NAME` | No | `deepseek-v4-flash` | Model identifier passed to the API |
| `BASE_URL` | No | `https://api.deepseek.com` | OpenAI-compatible provider base URL |
| `TEMPERATURE` | No | `0.5` | Sampling temperature (0–2) |
| `MAX_TOKENS` | No | `2048` | Max tokens per LLM response |
| `TIMEOUT_SECONDS` | No | `30.0` | HTTP timeout for LLM calls |
| `GOAT_LOG_DIR` | No | `/tmp/goat2/logs` | Directory for the rotating log file |

Minimum `.env` to run the bot:

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

**Letta** — required for permanent memory (L0 identity, L1 facts). Edit `config/memory.toml` to point at your instance:

```toml
[permanent]
letta_url = "http://localhost:8283"   # change host/port here
```

If Letta is unreachable at startup, L1 returns an empty facts dict and the bot continues with a warning per turn (see startup log section below). L0 identity still loads from `[identity] base_prompt` in `memory.toml`.

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
prefetch_timeout = 0.5

[retrieval_budget]
l3_min_guarantee_tokens = 1200
l3_gap_significance = 3.0

[session_cache]
ttl_seconds = 300
```

---

## Run the bot

```bash
python3 -m telegram_interface
```

Or equivalently:

```bash
python3 -m telegram_interface.__main__
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

## MCP server (optional)

An MCP server exposing memory introspection tools is included at `mcp_server/`. Run it with:

```bash
python3 -m mcp_server
```

This is independent of the Telegram bot and not required for normal operation.
