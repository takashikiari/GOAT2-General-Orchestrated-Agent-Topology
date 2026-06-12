---
description: Launch and drive the GOAT 2.0 interactive CLI chat loop for manual testing or verification
---

# Run GOAT 2.0

## Prerequisites

### 1. Redis (required for working memory)
```bash
# Check if running
redis-cli ping   # expects PONG

# Start if not running
sudo systemctl start redis   # systemd
# OR: redis-server --daemonize yes
```

### 2. API key (at least one required)
```bash
# DeepSeek (default model — see config/goat.toml [model])
export DEEPSEEK_API_KEY="sk-..."

# OR OpenAI
export OPENAI_API_KEY="sk-..."

# OR Groq (used by summarizer/memory agents)
export GROQ_API_KEY="gsk_..."
```

### 3. Python dependencies
```bash
cd /home/lenovo/workspace/goat2
pip install -r requirements-minimal.txt -q
```

## Launch

```bash
cd /home/lenovo/workspace/goat2
python3 cli.py
```

The CLI prints `GOAT 2.0 ready` and shows `you>` prompt.

## Smoke test sequence

Send these inputs in order to exercise the key paths:

```
you> hello
```
Expected: CONVERSATIONAL reply (no DAG). Tests identity + memory path.

```
you> what files are in the current workspace?
```
Expected: ANALYTICAL/COMPLEX DAG run. Tests `tool_caller` agent + working memory writes.

```
you> research the latest news on AI agents
```
Expected: COMPLEX DAG with `researcher` + `summarizer`. Tests DagPrompt builder + Level 1 verifier.

```
you> exit
```

## Verify new Task 1-5 behaviour

To confirm DagPrompt and clarification gate are active, set `verbose = true` in
`config/goat.toml` `[supervisor]` and watch logs for:

- `dag_execution: DagPrompt written task_id=...` — Task 1 + 3
- `GOAT: intent unclear — returning clarification request` — Task 2
- `ToolVerifier:` lines — Task 4 Level 1
- `dag:<session>:task:<tid>:status written` — Task 5

## Non-interactive smoke (no Redis/API key needed)

```bash
cd /home/lenovo/workspace/goat2
python3 -c "
from config.registry import ServiceRegistry
from tools.registry_accessor import set_registry
from supervisor.supervisor import GoatSupervisor
from supervisor.pipeline.dag_prompt_builder import DagPrompt, build_dag_prompt
from supervisor.pipeline.tool_verifier import VerifierReport, run_tool_verifier
from supervisor.pipeline.intent_clarity import check_intent_clarity
r = ServiceRegistry()
set_registry(r)
g = GoatSupervisor(r)
print('ok — all Task 1-5 modules imported, GoatSupervisor ready')
"
```

## Known setup notes

- Redis default: `redis://localhost:6379/0` (see `memory/working/redis_conn.py`)
- Letta (long-term memory) is optional; supervisor degrades gracefully when unavailable
- ChromaDB persists to `./chroma_db` by default (override: `[memory] chroma_persist_dir` in goat.toml)
- Telegram bot: set `TELEGRAM_BOT_TOKEN` env var and `telegram_enabled = true` in goat.toml; launch via `start_telegram.sh`
