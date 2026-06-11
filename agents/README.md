# agents/ — GOAT 2.0 Agent Layer

`BaseAgent` is the foundation. Subclass it, set `role`, implement `execute()`.
The instance is immediately usable as an `AgentRunner` (via `__call__`).

The package exports all **7 built-in agents** that the supervisor registry wires
into the DAG, plus the primitives and legacy critique helpers.

---

## Dependency Management (routing + TYPE_CHECKING + Registry)

agents/ must NEVER import from supervisor/ at module level. Three mechanisms
together enforce this:

1. **`from __future__ import annotations`** at the top of every file. All type
   hints are strings — the actual classes are looked up lazily, so the import
   that would be needed to resolve them never runs at import time.

2. **`if TYPE_CHECKING:` block** in every agents/ file. Cross-module type names
   (AgentResult, AgentTask, Registry) appear here as type-checker hints only;
   they are invisible to the runtime importer.

3. **Lazy / function-local imports** for anything that needs to be instantiated.
   This is how `tools.tool_runner`, `supervisor.identity`, and
   `supervisor.pipeline.plan_validator` are reached from agents/.

The single DI container lives in `config/registry.py` (`ServiceRegistry`). For
cross-module *values* that the lazy pattern can't help with — `SupervisorResult`,
the FILE_TOOLS / MEMORY_TOOLS / DAG_MEMORY_TOOLS convenience groups — use
`config/routing.py`:

```python
from config.routing import (
    get_supervisor_result,    # lazy: supervisor.types.SupervisorResult
    get_agent_result,         # lazy: config.agent_types.AgentResult
    get_agent_task,           # lazy: config.agent_types.AgentTask
    get_file_tools,           # lazy: tools.FILE_TOOLS
    get_memory_tools,         # lazy: tools.MEMORY_TOOLS
    get_dag_memory_tools,     # lazy: tools.DAG_MEMORY_TOOLS
    routing_debug_enabled,    # bool — toggle verbose routing logs
)
```

Each `get_*` accessor:
- Logs at DEBUG level on every call (`goat2.routing` logger).
- Performs the cross-module import inside the function body.
- If `routing_debug_enabled()` is true, additionally logs at INFO with the
  fully-qualified name of the resolved object.

Toggle verbose routing logs via:
- `GOAT_ROUTING_DEBUG=1` environment variable, or
- `[debug] routing = true` in `config/goat.toml`.

---

## Per-Agent Debug Logger

Every agent module declares its own logger under the `goat2.agents.*` namespace:

| Module | Logger name |
|---|---|
| `agents/base_agent.py` | `goat2.agents.base` |
| `agents/planner.py` | `goat2.agents.planner` |
| `agents/planner_decompose.py` | `goat2.agents.planner_decompose` |
| `agents/researcher.py` | `goat2.agents.researcher` |
| `agents/coder.py` | `goat2.agents.coder` |
| `agents/critic.py` | `goat2.agents.critic` |
| `agents/critique.py` | `goat2.agents.critique` |
| `agents/summarizer.py` | `goat2.agents.summarizer` |
| `agents/tool_caller.py` | `goat2.agents.tool_caller` |
| `agents/memory_agent.py` | `goat2.agents.memory` |

DEBUG events emitted:

- `__init__` — `log.debug("%s ready spec=%s tools=%s", ...)`
- `execute()` — at entry and at exit (with task_id and prompt/output length).
- `BaseAgent._dispatch_tool` — `log.debug("tool dispatched: %s args_keys=%s", ...)`.

Enable per-agent debugging:

```bash
# Verbose only for coder
LOG_LEVEL=DEBUG python -c "import logging; logging.getLogger('goat2.agents.coder').setLevel(logging.DEBUG)"

# Verbose for every agent
LOG_LEVEL=DEBUG
```

The config `LOG_LEVEL=DEBUG` (env var or toml) enables DEBUG across all loggers;
the per-agent namespace lets you raise verbosity for a single agent without
flooding the whole system.

---

## Directory Structure

```
agents/
├── __init__.py              # Re-exports all 7 agents + helpers (TYPE_CHECKING)
├── base_agent.py            # BaseAgent abstract class + @tool decorator
├── planner.py               # PlannerAgent
├── planner_decompose.py     # decompose_plan() — intent → task DAG
├── researcher.py            # ResearcherAgent
├── coder.py                 # CoderAgent (validate_syntax tool)
├── critic.py                # CriticAgent (extract_verdict, is_blocking)
├── critique.py              # critique_results(), synthesize_results()
├── summarizer.py            # SummarizerAgent
├── tool_caller.py           # ToolCallerAgent
├── memory_agent.py          # MemoryAgent
└── prompts/
    ├── __init__.py          # Exports RESEARCHER_SYSTEM
    └── researcher_prompt.py # System prompt for ResearcherAgent
```

---

## Agent Classes

| Class | File | Default model | Temp | Tools | Purpose |
|---|---|---|---|---|---|
| `PlannerAgent` | `planner.py` | gpt-4o | 0.3 | none | Structured plan output |
| `decompose_plan()` | `planner_decompose.py` | `supervisor.model` | 0.3 | n/a | Intent → task DAG |
| `ResearcherAgent` | `researcher.py` | deepseek-r1 | 0.3 | suppressed when R1 | Auto-suppresses tools for R1 |
| `CoderAgent` | `coder.py` | deepseek-coder | 0.2 | `validate_syntax` | Code generation + self-check |
| `CriticAgent` | `critic.py` | llama-3.3-70b | 0.3 | none | `extract_verdict()`, `is_blocking()` |
| `SummarizerAgent` | `summarizer.py` | llama-3.1-8b | 0.3 | none | Pure synthesis, no tools |
| `ToolCallerAgent` | `tool_caller.py` | deepseek-chat | 0.1 | 8 file + 4 DAG memory | Tool orchestration |
| `MemoryAgent` | `memory_agent.py` | (reuses tool_caller) | 0.1 | 4 DAG memory | Working-memory bridge |
| `critique_results()` | `critique.py` | critic | 0.2 | n/a | Review task results end-to-end |
| `synthesize_results()` | `critique.py` | planner | 0.5 | n/a | Final answer synthesis |

---

## Quick Start

```python
from agents.base_agent import BaseAgent
from config.settings import get_model

class LawyerAgent(BaseAgent):
    role = "lawyer"

    def __init__(self):
        super().__init__(spec=get_model("gpt-4o"), system_prompt=LAW_SYS, temperature=0.3)

    async def execute(self, task, context):
        return await self._chat(self._build_messages(task, context))
```

Register with the supervisor:

```python
sv.register_agent("lawyer", LawyerAgent())
```

---

## @tool Decorator

Marks a method as a callable tool. Auto-discovered at `__init__` and sent to the
LLM as an OpenAI function-calling schema. The `_chat` loop intercepts `tool_calls`,
dispatches them, appends results, and continues until the model returns plain text.

```python
@tool(name="validate_syntax", description="Validate code syntax", parameters={...})
async def _validate(self, code: str, language: str = "python") -> str: ...
```

**Tool calling compatibility:** OpenAI ✓ · Groq llama-3.3-70b ✓ · DeepSeek chat/coder ✓
DeepSeek reasoner (R1) ✗ — pass `tools=[]` to `_chat` (auto-suppressed in `ResearcherAgent`).

---

## BaseAgent Public Surface

| Method | Purpose |
|---|---|
| `execute(task, context)` | **Abstract** — implement in subclass |
| `__call__(task, context)` | Delegates to `execute`; satisfies `AgentRunner` protocol |
| `_chat(messages, *, temperature, json_mode, max_tool_rounds, tools)` | Agentic tool-calling loop |
| `_build_messages(task, context)` | Composes `[system, user]` from context + task prompt |
| `add_tool(name, desc, params, handler)` | Register a tool at runtime |
| `remove_tool(name)` | Deregister a tool |
| `tool_names` | `list[str]` of registered tool names |

---

## Prompt Templates (prompts/)

The `prompts/` subdirectory contains system prompts for different agent types.
These are imported from the parent `agents/` module:

```python
from agents import RESEARCHER_SYSTEM

# RESEARCHER_SYSTEM is the deep research prompt template
# Used by ResearcherAgent for investigation tasks
```

### prompts/researcher_prompt.py

Defines `_SYSTEM_PROMPT` — the system prompt for deep research agents.
Exported as `RESEARCHER_SYSTEM` from `agents/prompts/__init__.py`.

Structure:
- Summary: 2–3 sentences on the topic
- Key Findings: Numbered substantive findings with evidence
- Trade-offs & Failure Modes: Known limitations and edge cases
- Alternatives Considered: Other approaches and when to prefer them
- Recommendations: Concrete next steps with action verbs

---

## AgentRegistry Wiring

The supervisor wires all 7 built-in agents via
`supervisor.registry._build_default_registry()`:

```python
# supervisor/registry.py (excerpt)
def _build_default_registry() -> AgentRegistry:
    """Construct the default AgentRegistry with 7 built-in DAG runners."""
    registry = AgentRegistry()
    registry.register("planner",     _run_planner)
    registry.register("researcher",  _run_researcher)
    registry.register("coder",       _run_coder)
    registry.register("critic",      _run_critic)
    registry.register("summarizer",  _run_summarizer)
    registry.register("tool_caller", _run_tool_caller)
    registry.register("memory",      _run_memory)
    return registry
```

This is invoked by `ServiceRegistry.__init__` (via lazy import — see
`config/registry.py`) and the resulting registry is stored in
`ServiceRegistry.agent_registry`. The supervisor's workflow calls
`registry.get(role)` to obtain the runner for each task.

### Adding an 8th Agent

To register a new role (e.g. `lawyer`):

1. **Create the agent class** in `agents/lawyer.py` (subclass `BaseAgent`).
2. **Re-export** it in `agents/__init__.py` under TYPE_CHECKING if needed.
3. **Register the runner** in `supervisor/registry.py._build_default_registry()`:
   ```python
   from agents.lawyer import LawyerAgent
   registry.register("lawyer", LawyerAgent())
   ```
4. **Add the role** to `config/agents.py:AGENT_ROLES` so the planner knows
   it's a valid role string.

---

## Role Execution Patterns

| Role | Tool Access | Source | Description |
|---|---|---|---|
| `researcher` | web_search only | net | Deep research, forces web search |
| `coder` | FILE_TOOLS | file/net/generated | Code generation with file tools |
| `critic` | none | generated | Critical review |
| `summarizer` | none | generated | Synthesis |
| `tool_caller` | ALL_TOOLS | file/net/generated | General orchestration |
| `planner` | none | generated | Task decomposition |
| `memory` | memory tools | file/generated | Memory operations |

---

## config/agents.py Registry

The canonical agent role registry is in `config/agents.py`:

```python
from config.agents import (
    AGENT_ROLES,          # All valid roles
    EXECUTION_ROLES,     # Roles requiring tool calls
    SYNTHESIS_ROLES,     # Roles generating content
    DEFAULT_AGENT_ROLE,  # Fallback role
)
```

### AGENT_ROLES

Complete list of valid agent roles:
- `researcher` — deep web research
- `coder` — code generation
- `critic` — review and assessment
- `planner` — task decomposition
- `summarizer` — synthesis
- `tool_caller` — general tool orchestration
- `memory` — memory operations

### EXECUTION_ROLES

Roles that MUST invoke a real tool call. Generated output is never acceptable.
The DAG validator marks these as UNVERIFIED if tool_called=False.

```python
EXECUTION_ROLES = frozenset({"researcher", "tool_caller", "memory"})
```

### SYNTHESIS_ROLES

Roles where source=generated is valid. These produce content via LLM
inference without calling external tools.

```python
SYNTHESIS_ROLES = frozenset({"summarizer", "critic", "planner"})
```

### DEFAULT_AGENT_ROLE

Fallback role when none specified:

```python
DEFAULT_AGENT_ROLE = "tool_caller"
```

---

## Import Examples

### Import agent classes

```python
from agents import (
    BaseAgent,
    CoderAgent,
    CriticAgent,
    PlannerAgent,
    ResearcherAgent,
    SummarizerAgent,
    ToolCallerAgent,
    MemoryAgent,
)
```

### Import prompt templates

```python
from agents import RESEARCHER_SYSTEM  # Deep research prompt

# Or directly from prompts/
from agents.prompts import RESEARCHER_SYSTEM
```

### Import role constants

```python
from config.agents import (
    AGENT_ROLES,
    EXECUTION_ROLES,
    SYNTHESIS_ROLES,
    DEFAULT_AGENT_ROLE,
)

# Check if a role requires tool calls
if role in EXECUTION_ROLES:
    # Must enforce tool_choice='required'
    pass
```

### Import ToolDefinition

```python
from agents.base_agent import ToolDefinition, tool
```

### Cross-module routing

```python
from config.routing import (
    get_supervisor_result,
    get_agent_result,
    get_agent_task,
    get_file_tools,
    get_memory_tools,
    get_dag_memory_tools,
)
```

---

## Model Override

Override model at construction:

```python
from config.settings import get_model

CoderAgent(spec=get_model("gpt-4o"))
```

---

## See Also

- `supervisor/workflow.py` — DAG execution engine
- `supervisor/dag_validator.py` — Result validation
- `supervisor/registry.py` — `_build_default_registry()` for 7 built-in agents
- `config/agents.py` — Role registry
- `config/settings.py` — Model specifications
- `config/routing.py` — Lazy cross-module accessors
- `config/registry.py` — ServiceRegistry (single DI container)
