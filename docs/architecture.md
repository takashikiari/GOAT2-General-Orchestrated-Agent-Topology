# GOAT Architecture

## System Overview

GOAT (General Orchestrator and Agent Taskmaster) is a multi-agent system with three-tier
persistent memory, intelligent intent routing, and a full tool-calling system.

### Core Principles

1. **Memory Separation** — Three tiers with strict access control
2. **Agent Isolation** — DAG agents have limited scope; GOAT supervises all
3. **Anti-Hallucination** — Data flows only through verified paths
4. **Source Provenance** — Every output tagged with its origin

---

## Memory Architecture

### Three-Tier Memory

```
┌─────────────────────────────────────────────────────────────┐
│                    GOAT (Supervisor)                         │
│  ┌────────────────┐  ┌──────────────┐  ┌──────────────┐    │
│  │   Working      │  │   Episodic   │  │  Long-term   │    │
│  │   (Redis)      │  │  (ChromaDB)  │  │   (Letta)    │    │
│  │                │  │              │  │              │    │
│  │ • Session ctx  │  │ • Past turns │  │ • User pref  │    │
│  │ • Active conv  │  │ • Histories  │  │ • Profiles   │    │
│  │ • Tool output  │  │ • Patterns   │  │ • Long-term  │    │
│  │ • DAG bridge   │  │              │  │   memories   │    │
│  └───────┬────────┘  └──────────────┘  └──────────────┘    │
│          │                                                   │
│          │ Redis (bridge)                                    │
│          ▼                                                   │
│  ┌──────────────────────────────────────────────────┐        │
│  │                DAG Agents                         │        │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐          │        │
│  │  │ Planner  │ │Researcher│ │  Coder   │          │        │
│  │  └──────────┘ └──────────┘ └──────────┘          │        │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐          │        │
│  │  │  Critic  │ │Summarizer│ │Tool Call │          │        │
│  │  └──────────┘ └──────────┘ └──────────┘          │        │
│  │  ┌──────────┐                                     │        │
│  │  │ Memory   │ ← Redis bridge                      │        │
│  │  └──────────┘                                     │        │
│  └──────────────────────────────────────────────────┘        │
└─────────────────────────────────────────────────────────────┘
```

### Access Control

| Actor | Working (Redis) | Episodic (ChromaDB) | Long-term (Letta) |
|-------|----------------|---------------------|-------------------|
| **GOAT** | ✅ Full R/W | ✅ Full R/W | ✅ Full R/W |
| **DAG Agents** | ✅ Redis only | ❌ | ❌ |
| **Memory Agent** | ✅ Redis (bridge) | ❌ (query via GOAT) | ❌ (query via GOAT) |

### Memory Agent — Redis Bridge

Memory agent este un DAG agent special care:

1. **Scrie în Redis** — comunică cu ceilalți agenți prin working memory
2. **Își ia context** din working memory pentru task-uri ample
3. **Nu are acces direct** la Episodic (ChromaDB) sau Long-term (Letta)
4. **Query către GOAT** — dacă are nevoie de informații din straturile profunde, face request către GOAT
5. **GOAT filtrează** — decide ce informații să returneze, cât, și dacă e relevant
6. **Zero halucinații** — memory agent nu primește niciodată date nevăzute sau nefiltrate

### Data Flow

```
User Input → GOAT (intent routing)
  → DAG Pipeline (if complex task)
      → Planner → [Researcher | Coder | Tool Caller]
      → Critic → Summarizer
      → Results back to Redis (working)
  → GOAT reads from Redis
  → GOAT may promote to Episodic / Long-term
  → GOAT responds to user
```

---

## GOAT Supervisor vs DAG Agents

### GOAT (supervisor/assistant)

- **Full access** to all three memory backends: **Redis** (working), **ChromaDB** (episodic), **Letta** (long-term)
- Uses `MEMORY_TOOLS` (16 tools) with full tier access
- Memory tools have `tier` parameter accepting `any`, `working`, `episodic`, `long_term`
- Reads recent turns, session context, user profile directly — no tool calls needed
- Validates task success by checking tool parameters
- **Singurul care scrie în Letta (long-term)**

### DAG (agents — planner, researcher, coder, critic, summarizer, tool_caller, memory)

- **Redis read/write only** — DAG agents access **working** memory tier only
- **No access** to ChromaDB (episodic) or Letta (long-term)
- Uses `DAG_MEMORY_TOOLS` (4 tools) - **no `tier` parameter**:
  - `memory_search` - search working memory only
  - `memory_get` - get from working memory only
  - `memory_store` - store to working memory only
  - `memory_recent` - recent working memory entries only
- System prompt explicitly states: "Memory (working tier only): memory_search, memory_get, memory_store, memory_recent"
- Tool parameters validated by GOAT before marking tasks successful

---

## Tool System

### Tool Categories

| Category | Count | Access |
|----------|-------|--------|
| File Tools | 9 | All agents |
| Web Search | 1 | All agents |
| Shell | 1 | DAG only |
| Memory Tools (GOAT) | 16 | GOAT only (full tier) |
| Memory Tools (DAG) | 4 | DAG only (working tier) |
| **Total** | **26** | |

### GOAT Memory Tools (16)

Full tier access — can read/write to working, episodic, long-term:

- `MEMORY_SEARCH` — semantic search across tiers
- `MEMORY_GET` — exact-key lookup
- `MEMORY_STORE` — write to specified tier
- `MEMORY_DELETE` — delete entry by key
- `MEMORY_UPDATE` — update existing entry
- `MEMORY_TIMELINE` — entries in time range
- `MEMORY_RECENT` — most recent entries
- `MEMORY_DEBUG_TRACE` — per-tier debug JSON
- `MEMORY_DIRECT_QUERY` — raw queries to Letta/ChromaDB/Redis
- `MEMORY_LAST_WRITE` — check last-write timestamp
- `MEMORY_COUNT` — count entries in tier
- `MEMORY_TTL` — get/set TTL for entries
- `MEMORY_EMBEDDING` — get embedding vector
- `MEMORY_EXPORT` — export tier entries
- `MEMORY_PROMOTE` — promote entry between tiers
- `MEMORY_AUTO_PROMOTE` — auto-promote based on TTL

### DAG Memory Tools (4)

Working tier only — no tier parameter:

- `memory_search` — search working memory
- `memory_get` — get from working memory
- `memory_store` — store to working memory
- `memory_recent` — recent working memory entries

### File Tools

All agents have access to file operations:

- `FILE_READ`, `FILE_WRITE`, `FILE_CREATE`, `FILE_LIST`, `FILE_SEARCH`
- `FILE_GREP`, `FILE_INFO`, `FILE_READ_LINES`
- `WEB_SEARCH`, `SHELL` (DAG only)

---

## Source Provenance

Every tool call is tagged with a data source: **net**, **memory**, **file**, or **generated**.

### Validation Rules

GOAT supervisor validates task success by checking:
- `tool_called` is True
- `tool_name` is non-empty
- `raw_output_hash` is non-empty (proves tool execution)

If any parameter is missing, task is marked `validated=False` and synthesis is skipped.

| Role | Allowed sources |
|------|----------------|
| `researcher` | `net` only |
| `memory` | `memory` only |
| `coder` | `file`, `net`, `memory`, `generated` |
| `tool_caller` | `file`, `net`, `memory`, `generated` |
| `critic` / `summarizer` | `generated`, `file`, `memory` |
| `planner` | `generated` |

---

## Security

- Workspace root: `GOAT_WORKSPACE` env var or project root
- Blocks: dotdot traversal, symlink escape, sensitive files (`.env`, `id_rsa`, `.pem`, etc.)
- Atomic writes via `tempfile.NamedTemporaryFile` + `os.replace`
- `GOAT_ALLOW_OUTSIDE_WORKSPACE=true` + `GOAT_ALLOWED_PATHS` allowlist

---

## DAG Agent Roster (all 7)

The supervisor `AgentRegistry` (`supervisor/registry.py`) self-registers
all 7 built-in agents in its constructor. Each has its own logger under
`goat2.agents.<role>` for per-agent observability. The legacy helper
`_build_default_registry()` is preserved as a thin wrapper that
returns `AgentRegistry()`.

| Role | Class | Default model | Temp | Tools |
|---|---|---|---|---|
| `planner` | `PlannerAgent` | gpt-4o | 0.3 | none |
| `researcher` | `ResearcherAgent` | deepseek-r1 | 0.3 | suppressed when R1 |
| `coder` | `CoderAgent` | deepseek-coder | 0.2 | `validate_syntax` |
| `critic` | `CriticAgent` | llama-3.3-70b | 0.3 | none |
| `summarizer` | `SummarizerAgent` | llama-3.1-8b | 0.3 | none |
| `tool_caller` | `ToolCallerAgent` | deepseek-chat | 0.1 | 8 file + 4 DAG memory |
| `memory` | `MemoryAgent` | (reuses tool_caller) | 0.1 | 4 DAG memory |

Each class lives in its own `agents/*.py` file. The package re-exports all
7 from `agents/__init__.py` so the typical import is:

```python
from agents import (
    PlannerAgent, ResearcherAgent, CoderAgent, CriticAgent,
    SummarizerAgent, ToolCallerAgent, MemoryAgent,
)
```

---

## Dependency Management (routing + TYPE_CHECKING + Registry)

GOAT 2.0 is split into three layers that must not import each other at
module level: `agents/`, `supervisor/`, and `tools/`. A naive cross-module
import risks a circular chain through the `ServiceRegistry` initialization.
Four mechanisms together enforce the boundary:

### 1. `from __future__ import annotations`

Every file in `agents/` starts with this directive. All type hints become
strings, so the actual classes are looked up lazily and the import that
would be needed to resolve them never runs at import time.

### 2. `if TYPE_CHECKING:` blocks

Every `agents/*.py` file declares its cross-module type names
(`AgentResult`, `AgentTask`, `Registry`) inside a TYPE_CHECKING block. These
are visible to type checkers (mypy, pyright) but invisible to the runtime
importer — so the cycle is broken even when the type is referenced.

```python
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config.agent_types import AgentResult, AgentTask
    from config.registry import Registry
```

### 3. Lazy / function-local imports

For values that must be instantiated at runtime (not just hinted at),
agents/ uses lazy imports inside the function body:

```python
# agents/planner_decompose.py
async def decompose_plan(intent: str, registry: "Registry") -> Plan:
    # Lazy import — only resolved when decompose_plan is called
    from supervisor.pipeline.plan_validator import validate_plan
    ...
```

```python
# agents/tool_caller.py
def __init__(self, spec):
    # Lazy import — breaks tools -> agents -> tools cycle
    from tools import FILE_READ, MEMORY_RECENT_DAG, ...
    ...
```

### 4. Central routing layer — `config/routing.py`

For cross-module *values* the lazy pattern is awkward (you'd repeat the
import in many call sites). `config/routing.py` centralises them:

```python
from config.routing import (
    routing_debug_enabled,    # bool — toggle verbose routing logs
    get_agent_registry,       # AgentRegistry — all 7 DAG runners
    get_supervisor_result,    # SupervisorResult class
    get_agent_result,         # AgentResult class
    get_agent_task,           # AgentTask class
    get_file_tools,           # tools.FILE_TOOLS list
    get_memory_tools,         # tools.MEMORY_TOOLS list (GOAT full-tier)
    get_dag_memory_tools,     # tools.DAG_MEMORY_TOOLS list (DAG working-tier)
)
```

Each accessor logs at DEBUG level on every call (`goat2.routing` logger).
When `routing_debug_enabled()` is true, additionally logs at INFO with the
fully-qualified name of the resolved object. Toggle via:

- `GOAT_ROUTING_DEBUG=1` environment variable, or
- `[debug] routing = true` in `config/goat.toml`.

### 5. The single DI container — `config/registry.py`

`ServiceRegistry` is the **only** module-level container in the system.
Components receive a `registry` parameter explicitly; nothing else is a
singleton.

```python
from config.registry import ServiceRegistry

registry = ServiceRegistry()
supervisor = GoatSupervisor(registry=registry)
result = await supervisor.run("Build a REST API")
```

The registry is constructed once at application startup and passed to every
component. The cycle risk is contained to registry initialization
(`ServiceRegistry.__init__` lazy-imports `AgentRegistry` from supervisor/).

### 6. The cross-wiring — `ServiceRegistry` ↔ `AgentRegistry`

`config/registry.py` performs a **function-local** import of
`supervisor.registry.AgentRegistry` inside `__init__`. This is the
*only* cross-layer import in `config/`, and because it lives inside
the constructor body, the file can be imported at startup without
pulling in `supervisor/`, `agents/`, or `tools/`. The type hint is
declared under `TYPE_CHECKING`.

```python
# config/registry.py
if TYPE_CHECKING:
    from supervisor.registry import AgentRegistry

class ServiceRegistry:
    def __init__(self, config_path: str = "config/goat.toml") -> None:
        ...
        # 7. Agent registry — function-local import, NOT at module level
        from supervisor.registry import AgentRegistry
        self.agent_registry: AgentRegistry = AgentRegistry()
```

The constructed `AgentRegistry` self-registers all 7 DAG runners in its
own `__init__`, so the parent `ServiceRegistry` needs no further wiring.

---

## ServiceRegistry ↔ AgentRegistry Relationship

```
ServiceRegistry (config/registry.py, the only module-level container)
  ├── settings             Settings (env + toml resolution)
  ├── working_memory       WorkingMemoryLayer (Redis-backed)
  ├── memory_manager       MemoryManager (working + long_term tiers)
  ├── letta_client         LettaClient (long-term memory)
  ├── file_tools           [ToolDefinition] — file + web + shell
  ├── memory_tools         [ToolDefinition] — 16 GOAT full-tier tools
  ├── dag_memory_tools     [ToolDefinition] — 4 DAG working-tier tools
  ├── agent_models         AgentModels (per-role model keys)
  └── agent_registry  ──►  AgentRegistry (supervisor/registry.py, NOT a singleton)
                              ├── researcher   → _run_researcher
                              ├── coder        → _run_coder
                              ├── critic       → _run_critic
                              ├── planner      → _run_planner
                              ├── summarizer   → _run_summarizer
                              ├── tool_caller  → _run_tool_caller
                              └── memory       → _run_memory
```

### Why two registries?

- **`ServiceRegistry`** owns **infrastructure** (memory, settings, tools,
  models) and the lifetime of the application. It is instantiated
  once at startup.
- **`AgentRegistry`** owns **agent wiring** (the role → runner mapping).
  `AgentRegistry()` self-initializes with the 7 defaults in its
  constructor. It is a regular class — instantiate as many as you
  need; the canonical instance lives at `ServiceRegistry.agent_registry`.

### Cross-layer access

Use the routing accessor when you need an `AgentRegistry` from a
context where importing `supervisor/` directly is awkward (e.g. from
`agents/` files):

```python
from config.routing import get_agent_registry

reg = get_agent_registry()       # lazy, function-local import
runner = reg.get("memory")
```

Use `ServiceRegistry` for the canonical instance during normal
application flow:

```python
from config.registry import ServiceRegistry
registry = ServiceRegistry()
runner = registry.get("memory")   # delegates to registry.agent_registry
```

---

## Debug & Observability per Module

Every module declares a logger under the `goat2.<layer>.<role>` namespace.
This makes per-agent, per-subsystem debugging trivial — set the level on
exactly the logger you care about.

| Module | Logger | DEBUG events |
|---|---|---|
| `agents/base_agent.py` | `goat2.agents.base` | tool dispatched, tool errors |
| `agents/planner.py` | `goat2.agents.planner` | agent ready, execute start/done |
| `agents/planner_decompose.py` | `goat2.agents.planner_decompose` | spec resolved, plan validated |
| `agents/researcher.py` | `goat2.agents.researcher` | agent ready, execute start/done |
| `agents/coder.py` | `goat2.agents.coder` | agent ready, execute start/done |
| `agents/critic.py` | `goat2.agents.critic` | agent ready, execute start/done |
| `agents/critique.py` | `goat2.agents.critique` | critique_results, synthesize_results |
| `agents/summarizer.py` | `goat2.agents.summarizer` | agent ready, execute start/done |
| `agents/tool_caller.py` | `goat2.agents.tool_caller` | agent ready, execute start/done |
| `agents/memory_agent.py` | `goat2.agents.memory` | agent ready, execute start/done |
| `config/__init__.py` | `goat2.config` | (parent logger for subtree) |
| `config/agent_models.py` | `goat2.config.agent_models` | model key resolution per role |
| `config/agents.py` | `goat2.config.agents` | (constants) |
| `config/api_keys.py` | `goat2.config.api_keys` | API key resolution (env / toml) |
| `config/limits.py` | `goat2.config.limits` | (constants) |
| `config/model_catalogue.py` | `goat2.config.model_catalogue` | unknown model lookup |
| `config/model_selector.py` | `goat2.model_selector` | health-check failures, fallback |
| `config/onboarding.py` | `goat2.config.onboarding` | (constants) |
| `config/registry.py` | `goat2.config.registry` | `ServiceRegistry` step-by-step init, `get(role)` |
| `config/roles.py` | `goat2.config.roles` | (constants) |
| `config/routing.py` | `goat2.routing` | every `get_*` accessor + FQN on debug |
| `config/settings.py` | `goat2.config.settings` | `_e()` resolution, `validate()` start/ok |
| `config/supervisor.py` | `goat2.config.supervisor` | (constants) |
| `config/tiers.py` | `goat2.config.tiers` | (constants) |
| `config/timeouts.py` | `goat2.config.timeouts` | (constants) |
| `config/tools.py` | `goat2.config.tools` | (constants) |
| `config/toml_loader.py` | `goat2.config.toml_loader` | toml loaded / not found / parse error |
| `config/memory.py` | `goat2.config.memory` | (re-export shim) |
| `supervisor/registry.py` | `goat2.supervisor.registry` | runner registered / looked up, init summary |
| `supervisor/pipeline/runners.py` | `goat2.runners` | tool selection per runner |

Enable DEBUG globally:

```bash
LOG_LEVEL=DEBUG python -m goat
```

Enable DEBUG for a single agent:

```python
import logging
logging.getLogger("goat2.agents.coder").setLevel(logging.DEBUG)
```

Enable verbose routing traces:

```bash
GOAT_ROUTING_DEBUG=1 python -m goat
# or in goat.toml:
# [debug]
# routing = true
```

---

## Zero Singleton Architecture

GOAT 2.0 has exactly one module-level object: the **`ServiceRegistry`** in
`config/registry.py`. Everything else is either:

- **A pure function** (no state) — e.g. `parse_verdict`, `extract_json`.
- **An instance passed explicitly** as a parameter — e.g.
  `GoatSupervisor(registry=registry)`, `PlannerAgent(spec=...)`.
- **A class-level constant** — e.g. `ModelSpec.provider` enum value,
  `ToolDefinition` records in `FILE_TOOLS`.

### The guarantee

The system has **exactly one** module-level container: the
`ServiceRegistry` defined in `config/registry.py`. The companion
`AgentRegistry` (`supervisor/registry.py`) is **not** a singleton —
it is a regular class, constructed freely via `AgentRegistry()`. The
canonical instance lives at `ServiceRegistry.agent_registry`, but
nothing prevents code from instantiating additional `AgentRegistry`
objects (e.g. for testing or per-session registries).

The `ServiceRegistry` itself is *not* a module-level instance — it is
a class, instantiated once at application startup and passed
explicitly through the call stack. The phrase "one module-level
object" means "one module-level **class** that serves as the
canonical DI container."

### What's NOT a singleton anymore

Earlier versions of GOAT had:

- `from config.settings import settings` (a module-level `Settings()` instance) — **REMOVED in Phase 4**.
- `from memory import memory_manager` (a global `MemoryManager`) — **REMOVED in Phase 4**.
- `from tools import registry_accessor.get_registry()` (a global registry accessor) — **REMOVED in Phase 4**.

All callers must now go through `ServiceRegistry` and pass it down the
call stack explicitly. This makes the system testable, hermetic, and free
of "where did this state come from?" surprises.

### Verification

To prove the system has no hidden singletons, run `agents` in isolation:

```python
import sys
from agents import (
    BaseAgent, PlannerAgent, ResearcherAgent, CoderAgent, CriticAgent,
    SummarizerAgent, ToolCallerAgent, MemoryAgent,
)
loaded = [m for m in sys.modules if m.startswith('supervisor')]
assert loaded == [], f"agents/ leaked supervisor modules: {loaded}"
```

This assertion passes because every supervisor-side dependency in
`agents/` is hidden behind a `TYPE_CHECKING` guard or a lazy import
inside a function body.

To prove `config.registry` doesn't leak `supervisor/` at import time:

```python
import sys
import config.registry
leaked = [m for m in sys.modules if m.startswith('supervisor')]
assert leaked == [], f"config/registry.py leaked supervisor modules at import: {leaked}"
```

This passes because the `from supervisor.registry import AgentRegistry`
inside `config/registry.py` lives inside `ServiceRegistry.__init__`,
not at module level. The `TYPE_CHECKING` block at the top only provides
type hints and never runs.
