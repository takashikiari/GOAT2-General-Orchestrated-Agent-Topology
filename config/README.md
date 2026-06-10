# config/ — GOAT 2.0 Configuration Layer

The `config/` module is the central registry for all constants used throughout GOAT 2.0.
It provides a single source of truth for agent roles, memory tiers, timeouts, limits, model specifications,
and API credentials. All files import from this module instead of hardcoding values.

## Philosophy

```
Resolution order (applied to every setting):
  environment variable  →  config/goat.toml  →  hard-coded default
```

Environment variables always take precedence over configuration files. This enables
CI/CD pipelines and production deployments to override defaults without modifying files.

## Directory Structure

```
config/
├── __init__.py           # Re-exports public API: Settings, Provider, ModelSpec, get_model
├── agents.py             # Agent role registry (AGENT_ROLES, EXECUTION_ROLES, etc.)
├── api_keys.py           # API credential management (APIKeys, PROVIDER_BASE_URLS)
├── goat.toml             # Primary configuration file (5 sections)
├── limits.py             # System limits and TTLs (MAX_RECALL_LIMIT, DAG_RESULT_TTL, etc.)
├── model_catalogue.py    # Model registry (Provider, ModelSpec, MODELS, get_model)
├── model_selector.py     # Dynamic model selection with fallback chains
├── registry.py           # ServiceRegistry (dependency injection container)
├── roles.py             # Memory namespace roles (GOAT_ROLE, SESSION_ROLE)
├── settings.py         # Settings loader (Settings, LettaConfig, SupervisorConfig)
├── tiers.py            # Memory tier constants (WORKING, EPISODIC, LONG_TERM, ANY)
├── timeouts.py          # Timeout values (TURN_TIMEOUT, TOOL_TIMEOUT, etc.)
├── toml_loader.py       # TOML loading utilities (TomlConfig, load_toml)
├── agent_models.py     # Per-role model configuration
├── README.md           # This file
```

---

## File Reference

### agents.py — Agent Role Registry

Central registry for all agent roles used in task decomposition and DAG validation.

```python
from config.agents import (
    AGENT_ROLES,          # ["researcher", "coder", "critic", "planner", "summarizer", "tool_caller", "memory"]
    EXECUTION_ROLES,     # frozenset({"researcher", "tool_caller", "memory"})
    SYNTHESIS_ROLES,    # frozenset({"summarizer", "critic", "planner"})
    DEFAULT_AGENT_ROLE, # "tool_caller"
)
```

**Constants:**
- `AGENT_ROLES`: Complete list of valid agent roles for task decomposition
- `EXECUTION_ROLES`: Roles that MUST invoke a real tool call (generated output unacceptable)
- `SYNTHESIS_ROLES`: Roles where source=generated is valid (no external tools)
- `DEFAULT_AGENT_ROLE`: Fallback role when none specified

**Usage in supervisor:**
```python
# Validate role in plan decomposition
if task.role not in AGENT_ROLES:
    raise ValueError(f"Invalid role: {task.role}")

# Check if role requires tool calls
if task.role in EXECUTION_ROLES:
    # Enforce tool_choice='required'
    pass
```

---

### api_keys.py — API Credential Management

API credentials with environment variable precedence over goat.toml.

```python
from config.api_keys import APIKeys, PROVIDER_BASE_URLS

# Initialize (env var takes precedence)
keys = APIKeys()

# Get key for provider
openai_key = keys.for_provider(Provider.OPENAI)

# Base URLs for HTTP clients
url = PROVIDER_BASE_URLS[Provider.DEEPSEEK]
```

**Classes:**
- `APIKeys`: Dataclass with openai, deepseek, groq fields. Resolves via env var → toml → empty.
- `PROVIDER_BASE_URLS`: Dict mapping Provider enum to base URL strings.

**Environment Variables:**
- `OPENAI_API_KEY`
- `DEEPSEEK_API_KEY`
- `GROQ_API_KEY`

---

### goat.toml — Main Configuration File

The primary local-dev configuration file. Copy `goat.toml.example` and edit; never commit real keys.

```toml
# Model section
[model]
default = "gpt-4o-mini"
supervisor = "gpt-4o"

# Per-agent model assignments
[agents]
planner = "gpt-4o"
researcher = "deepseek-r1"
coder = "deepseek-coder"

# API keys (local dev only)
[api_keys]
openai = ""
deepseek = ""

# Memory configuration
[memory]
letta_base_url = "http://localhost:8283"
letta_token_limit = 4096

# Future channel integrations
[channels]
telegram_enabled = false
```

**Sections:**
| Section | Purpose |
|---------|---------|
| `[model]` | Default and supervisor model keys |
| `[agents]` | Per-role model assignments |
| `[api_keys]` | Local API keys (blank = use env var) |
| `[memory]` | Letta and ChromaDB settings |
| `[channels]` | Future channel integrations |

---

### limits.py — System Limits and TTLs

Central registry for numeric limits and time-to-live values.

```python
from config.limits import (
    MAX_LINES_PER_FILE,     # 200 - max lines from file reads
    MAX_RECALL_LIMIT,      # 50 - max entries from memory queries
    MAX_TURNS_HISTORY,    # 20 - max conversation turns retained
    DAG_RESULT_TTL,       # 3600 - seconds (1 hour)
    WORKING_MEMORY_TTL,   # 3600 - seconds (1 hour)
    INFERRED_MEMORY_TTL,  # 604800 - seconds (7 days)
)
```

**Categories:**

*File Limits:*
- `MAX_LINES_PER_FILE`: Prevents overwhelming LLM context with large files

*Memory Limits:*
- `MAX_RECALL_LIMIT`: Balances recall quality with context window constraints

*Conversation Limits:*
- `MAX_TURNS_HISTORY`: Prevents unbounded memory growth

*TTL Values:*
- `DAG_RESULT_TTL`: Time-to-live for DAG execution results in Redis
- `WORKING_MEMORY_TTL`: Default TTL for working memory entries
- `INFERRED_MEMORY_TTL`: TTL for inferred facts in ChromaDB (7 days)

---

### model_catalogue.py — Model Registry

Immutable model descriptors with capability flags.

```python
from config.model_catalogue import Provider, ModelSpec, MODELS, get_model

# List available models
print(list(MODELS.keys()))
# ["gpt-4o", "gpt-4o-mini", "deepseek-chat", "deepseek-coder", "deepseek-r1", ...]

# Get model spec
spec = get_model("deepseek-coder")
print(spec.provider, spec.model_id, spec.tool_calling)
# Provider.DEEPSEEK deepseek-coder True
```

**Classes:**
- `Provider`: Enum (OPENAI, DEEPSEEK, GROQ)
- `ModelSpec`: Frozen dataclass with provider, model_id, tool_calling, no_temperature

**Model Catalogue:**

| Key | Provider | Model ID | tool_calling |
|-----|----------|---------|--------------|
| `gpt-4o` | OpenAI | `gpt-4o` | ✓ |
| `gpt-4o-mini` | OpenAI | `gpt-4o-mini` | ✓ |
| `gpt-4-turbo` | OpenAI | `gpt-4-turbo` | ✓ |
| `gpt-5.5` | OpenAI | `gpt-5.5` | ✓ (no_temp) |
| `deepseek-chat` | DeepSeek | `deepseek-chat` | ✓ |
| `deepseek-coder` | DeepSeek | `deepseek-coder` | ✓ |
| `deepseek-r1` | DeepSeek | `deepseek-reasoner` | ✗ |
| `llama-3.3-70b` | Groq | `llama-3.3-70b-versatile` | ✓ |
| `llama-3.1-8b` | Groq | `llama-3.1-8b-instant` | ✓ |
| `mixtral-8x7b` | Groq | `mixtral-8x7b-32768` | ✓ |

---

### model_selector.py — Dynamic Model Selection

Model selection with fallback priority chains and health checking.

```python
from config.model_selector import get_model_for_role, ModelUnavailableError

try:
    spec = get_model_for_role("planner")  # Returns first available model
except ModelUnavailableError as e:
    print(f"No models available: {e}")
```

**Features:**
- Priority list from goat.toml or defaults
- Health check (API key presence)
- Fallback to next model in chain
- Clear error when all models fail

**Default Fallbacks:**
```python
{
    "planner": ["deepseek-r1", "gpt-4o", "llama-3.3-70b"],
    "researcher": ["deepseek-chat", "gpt-4o-mini", "llama-3.3-70b"],
    "coder": ["deepseek-coder", "gpt-4o", "llama-3.3-70b"],
    "critic": ["llama-3.3-70b", "gpt-4o-mini", "deepseek-chat"],
    "summarizer": ["llama-3.1-8b", "gpt-4o-mini", "deepseek-chat"],
    "tool_caller": ["deepseek-chat", "gpt-4o-mini", "llama-3.3-70b"],
    "memory": ["llama-3.1-8b", "gpt-4o-mini", "deepseek-chat"],
}
```

---

### registry.py — Service Registry

Central dependency injection container for GOAT 2.0.

```python
from config.registry import ServiceRegistry

# Initialize once at startup
registry = ServiceRegistry(config_path="config/goat.toml")

# Pass to components
supervisor = GoatSupervisor(registry=registry)
```

**Services Owned:**
- `settings`: Settings configuration container
- `working_memory`: WorkingMemoryLayer (Redis-backed)
- `memory_manager`: MemoryManager coordinating all tiers
- `file_tools`: List of file operation ToolDefinitions
- `memory_tools`: List of memory operation ToolDefinitions
- `dag_memory_tools`: Restricted tools for DAG agents
- `agent_models`: AgentModels for per-role configuration
- `letta_client`: Letta client for long-term memory

---

### roles.py — Memory Namespace Roles

Central registry for memory access control roles.

```python
from config.roles import GOAT_ROLE, SESSION_ROLE

# GOAT_ROLE = "goat" — Supervisor with full tier access
# SESSION_ROLE = "user_session" — DAG agents with working-only access
```

**Roles:**
- `GOAT_ROLE`: Supervisor identity with full memory tier access
- `SESSION_ROLE`: Session-scoped role for DAG execution

---

### settings.py — Settings Loader

Main settings container with environment-based configuration.

```python
from config.settings import Settings, Provider, ModelSpec, get_model

# Initialize
settings = Settings()

# Access configuration
model = settings.supervisor.model  # ModelSpec for supervisor
keys = settings.api_keys           # APIKeys instance
letta = settings.letta            # LettaConfig

# Validate configuration
settings.validate()  # Raises EnvironmentError if misconfigured
```

**Dataclasses:**
- `LettaConfig`: Letta server configuration (base_url, api_key, embed_model, llm_model)
- `SupervisorConfig`: Supervisor settings (model_key, max_turns, max_workers, turn_timeout, temperature)
- `Settings`: Main container with all configuration sections

**Temperature Settings:**
| Component | Temperature | Purpose |
|-----------|-------------|---------|
| Supervisor | 0.5 | Accurate summaries, reduced hallucination |
| Default Agent | 0.4 | Balanced creativity/accuracy |
| Critic | 0.3 | Analytical consistency |
| Summarizer | 0.5 | Matches supervisor for accuracy |

---

### tiers.py — Memory Tier Constants

Central registry for memory tier identifiers.

```python
from config.tiers import WORKING, EPISODIC, LONG_TERM, ANY

# WORKING = "working" — Redis, session-scoped with TTL
# EPISODIC = "episodic" — ChromaDB, semantic search
# LONG_TERM = "long_term" — Letta, core memory blocks
# ANY = "any" — Search across all tiers
```

**Three-Tier Architecture:**

| Tier | Backing | Access | Purpose |
|------|---------|--------|---------|
| `WORKING` | Redis | Supervisor + DAG | Session context, DAG results |
| `EPISODIC` | ChromaDB | Supervisor only | Semantic search, persistent |
| `LONG_TERM` | Letta | Supervisor only | Core memory, identity |

---

### timeouts.py — Timeout Values

Central registry for async operation timeouts.

```python
from config.timeouts import (
    TURN_TIMEOUT,    # 120 seconds - per conversation turn
    TOOL_TIMEOUT,   # 30 seconds - tool execution
    REDIS_TIMEOUT, # 5 seconds - Redis connection
    LETTERA_TIMEOUT, # 8 seconds - Letta HTTP
)
```

**Philosophy:**
- Short timeouts for external services (fail fast)
- Longer timeouts for user-facing operations
- All should be configurable via environment variables

---

### toml_loader.py — TOML Loading Utilities

Typed accessors for goat.toml configuration.

```python
from config.toml_loader import load_toml, TomlConfig

# Load configuration
config = load_toml()

# Typed accessors
model_key = config.model("supervisor")      # String from [model]
agent_model = config.agent("planner")         # String from [agents]
api_key = config.api_key("openai")          # String from [api_keys]
letta_url = config.memory_str("letta_base_url")  # String from [memory]
redis_ttl = config.memory_int("ttl", 3600)     # Int from [memory] with default
```

**Methods:**
- `model(key, default)`: Get value from [model] section
- `agent(role)`: Get model key for agent role
- `api_key(provider)`: Get API key from [api_keys]
- `memory_str(key, default)`: Get string from [memory]
- `memory_int(key, default)`: Get integer from [memory]
- `channel_str(key, default)`: Get string from [channels]
- `channel_bool(key, default)`: Get boolean from [channels]

---

### agent_models.py — Per-Role Model Configuration

Model configuration resolution for agent roles.

```python
from config.agent_models import AgentModels

models = AgentModels()

# Get model for role (5-step resolution)
spec = models.get("planner")
# AGENT_PLANNER_MODEL env → DEFAULT_MODEL env → [agents].planner → [model].default → hard-coded

# List configured roles
print(models.roles)
# ["planner", "researcher", "coder", "critic", "summarizer", "tool_caller", "memory"]
```

**Resolution Chain:**
```
AGENT_<ROLE>_MODEL env  →  DEFAULT_MODEL env  →  [agents].<role>  →  [model].default  →  role hard-coded default
```

---

## How to Add New Constants

### Rule: Always in Registry Files

Never hardcode constants in component files. Add them to the appropriate registry:

1. **Agent roles** → `config/agents.py`
2. **Memory tiers** → `config/tiers.py`
3. **Timeouts** → `config/timeouts.py`
4. **Limits** → `config/limits.py`
5. **Roles** → `config/roles.py`

### Example: Adding a New Limit

1. Add to `config/limits.py`:
```python
MY_NEW_LIMIT: Final[int] = 100
"""Description of what this limit controls."""
```

2. Export in `__all__`:
```python
__all__ = [
    # ... existing exports ...
    "MY_NEW_LIMIT",
]
```

3. Import in component files:
```python
from config.limits import MY_NEW_LIMIT

# Use instead of hardcoding
result = process(items[:MY_NEW_LIMIT])
```

---

## Import Quick Reference

### Most Common Imports

```python
# Settings and configuration
from config import Settings, Provider, ModelSpec, get_model
from config.settings import Settings

# Agent configuration
from config.agents import AGENT_ROLES, EXECUTION_ROLES, SYNTHESIS_ROLES

# Memory configuration
from config.tiers import WORKING, EPISODIC, LONG_TERM, ANY
from config.roles import GOAT_ROLE, SESSION_ROLE

# Model selection
from config.model_catalogue import MODELS, get_model
from config.model_selector import get_model_for_role

# Limits and timeouts
from config.limits import MAX_RECALL_LIMIT, DAG_RESULT_TTL
from config.timeouts import TURN_TIMEOUT, TOOL_TIMEOUT

# Credentials
from config.api_keys import APIKeys, PROVIDER_BASE_URLS

# TOML configuration
from config.toml_loader import load_toml
```

---

## Validation

Always validate configuration at startup:

```python
from config.settings import Settings

settings = Settings()
settings.validate()  # Raises EnvironmentError with all missing credentials
```

---

## See Also

- `supervisor/workflow.py` — DAG execution using agent roles
- `supervisor/dag_validator.py` — Uses EXECUTION_ROLES for validation
- `agents/` — Agent implementations using model specs
- `memory/` — Memory tiers using tier constants