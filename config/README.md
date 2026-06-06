# config/ — Configuration Layer

```python
from config.settings import settings, get_model, MODELS
settings.validate()   # fail fast: list all missing API keys before starting a run
```

## Primary config: `config/goat.toml`

`goat.toml` is the single local-dev config file. Copy and edit it; never commit real keys.
Env vars always override toml values — set them in CI/production instead of editing the file.

```
Resolution order (applied to every setting):
  environment variable  →  goat.toml  →  hard-coded default
```

## goat.toml sections

| Section | Purpose |
|---------|---------|
| `[model]` | `default` (all-agent fallback), `provider` (informational), `supervisor` model |
| `[agents]` | Per-role model keys — same names as agent roles |
| `[api_keys]` | Local-dev only. Blank = rely on env var. **Never commit real keys.** |
| `[memory]` | Letta + ChromaDB settings |
| `[channels]` | Future interfaces (Telegram, …) |

## Settings hierarchy

```
Settings
├── api_keys:         APIKeys          env OPENAI_API_KEY / [api_keys].openai, …
├── letta:            LettaConfig      env LETTA_BASE_URL / [memory].letta_base_url, …
├── supervisor:       SupervisorConfig env SUPERVISOR_MODEL / [model].supervisor
├── agents:           AgentModels      env AGENT_*_MODEL / [agents].* / DEFAULT_MODEL
├── default_model:    str              env DEFAULT_MODEL / [model].default
└── default_provider: str              env DEFAULT_PROVIDER / [model].provider
```

## Agent model resolution (5-step chain)

```
AGENT_<ROLE>_MODEL env  →  DEFAULT_MODEL env  →  [agents].<role>  →  [model].default  →  role hard-coded default
```

`DEFAULT_MODEL=gpt-4o-mini` switches every agent in one env var (still overridden per-role).

## Key environment variables

| Variable | goat.toml key | Default | Used by |
|----------|---------------|---------|---------|
| `DEFAULT_MODEL` | `[model].default` | — | fallback for all agents and supervisor |
| `DEFAULT_PROVIDER` | `[model].provider` | — | validated in `Settings.validate()` |
| `OPENAI_API_KEY` | `[api_keys].openai` | — | planner, conversational, classifier |
| `DEEPSEEK_API_KEY` | `[api_keys].deepseek` | — | researcher, coder |
| `GROQ_API_KEY` | `[api_keys].groq` | — | critic, summarizer |
| `SUPERVISOR_MODEL` | `[model].supervisor` | `gpt-4o` | `decompose_plan` |
| `AGENT_PLANNER_MODEL` | `[agents].planner` | `gpt-4o` | `PlannerAgent` |
| `AGENT_RESEARCHER_MODEL` | `[agents].researcher` | `deepseek-r1` | `ResearcherAgent` |
| `AGENT_CODER_MODEL` | `[agents].coder` | `deepseek-coder` | `CoderAgent` |
| `AGENT_CRITIC_MODEL` | `[agents].critic` | `llama-3.3-70b` | `CriticAgent` |
| `AGENT_SUMMARIZER_MODEL` | `[agents].summarizer` | `llama-3.1-8b` | `synthesize_results` |
| `AGENT_MEMORY_MODEL` | `[agents].memory` | `gpt-4o-mini` | classifier, info_extract |
| `LETTA_BASE_URL` | `[memory].letta_base_url` | `http://localhost:8283` | Letta ops |
| `LETTA_LLM_MODEL` | `[memory].letta_llm_model` | `openai/gpt-4o-mini` | Letta agents |
| `LETTA_MEMORY_TOKEN_LIMIT` | `[memory].letta_token_limit` | `4096` | block token limit |

## Model catalogue (`MODELS` dict)

| Key | Provider | Model ID | tool_calling |
|-----|----------|----------|--------------|
| `gpt-4o` | OpenAI | `gpt-4o` | ✓ |
| `gpt-4o-mini` | OpenAI | `gpt-4o-mini` | ✓ |
| `gpt-4-turbo` | OpenAI | `gpt-4-turbo` | ✓ |
| `deepseek-r1` | DeepSeek | `deepseek-reasoner` | ✗ |
| `deepseek-coder` | DeepSeek | `deepseek-coder` | ✓ |
| `llama-3.3-70b` | Groq | `llama-3.3-70b-versatile` | ✓ |
| `llama-3.1-8b` | Groq | `llama-3.1-8b-instant` | ✓ |
| `mixtral-8x7b` | Groq | `mixtral-8x7b-32768` | ✓ |

## Module map

| File | Responsibility |
|------|----------------|
| `goat.toml` | Primary local-dev config — 5 sections |
| `toml_loader.py` | `_load_raw()`, `TomlConfig` typed accessors, `load_toml()` |
| `model_catalogue.py` | `Provider`, `ModelSpec` (+ `tool_calling`), `MODELS`, `get_model()` |
| `api_keys.py` | `APIKeys` (env → toml), `PROVIDER_BASE_URLS` |
| `agent_models.py` | `AgentModels` — 5-step model key resolution |
| `settings.py` | `LettaConfig`, `SupervisorConfig`, `Settings`, singleton; re-exports all names |

All callers import from `config.settings` — the sub-module split is internal.
