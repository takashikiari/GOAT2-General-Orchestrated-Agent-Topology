# agents/ — GOAT 2.0 Agent Layer

`BaseAgent` is the foundation. Subclass it, set `role`, implement `execute()`.
The instance is immediately usable as an `AgentRunner` (via `__call__`).

## Directory Structure

```
agents/
├── __init__.py              # Re-exports all public agent classes and prompts
├── base_agent.py           # BaseAgent abstract class + @tool decorator
├── planner.py              # PlannerAgent for task decomposition
├── planner_decompose.py    # decompose_plan() - intent to task DAG
├── researcher.py         # ResearcherAgent for web research
├── coder.py              # CoderAgent for code generation
├── critic.py             # CriticAgent for review/assessment
├── critique.py          # critique_results(), synthesize_results()
├── prompts/
│   ├── __init__.py           # Exports RESEARCHER_SYSTEM
│   └── researcher_prompt.py   # System prompt for ResearcherAgent
```

## Agent Classes

| Class | File | Model | Temp | Purpose |
|-------|------|-------|------|---------|
| `PlannerAgent` | `planner.py` | gpt-4o | 0.3 | Structured plan output |
| `decompose_plan()` | `planner_decompose.py` | supervisor.model | 0.3 | Intent to task DAG |
| `ResearcherAgent` | `researcher.py` | deepseek-reasoner | 0.3 | Auto-suppresses tools for R1 model |
| `CoderAgent` | `coder.py` | deepseek-coder | 0.2 | `validate_syntax` tool |
| `CriticAgent` | `critic.py` | llama-3.3-70b | 0.3 | `extract_verdict()`, `is_blocking()` |
| `critique_results()` | `critique.py` | critic | 0.2 | Review task results |
| `synthesize_results()` | `critique.py` | planner | 0.5 | Final answer synthesis |

## Quick Start

```python
from agents.base_agent import BaseAgent, tool
from config.settings import settings

class LawyerAgent(BaseAgent):
    role = "lawyer"

    def __init__(self):
        super().__init__(spec=settings.agents.get("planner"), temperature=0.3)

    async def execute(self, task, context):
        return await self._chat(self._build_messages(task, context))
```

Register with the supervisor:

```python
sv.register_agent("lawyer", LawyerAgent())
```

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

## BaseAgent Public Surface

| Method | Purpose |
|--------|---------|
| `execute(task, context)` | **Abstract** — implement in subclass |
| `__call__(task, context)` | Delegates to `execute`; satisfies `AgentRunner` protocol |
| `_chat(messages, *, temperature, json_mode, max_tool_rounds, tools)` | Agentic tool-calling loop |
| `_build_messages(task, context)` | Composes `[system, user]` from context + task prompt |
| `add_tool(name, desc, params, handler)` | Register a tool at runtime |
| `remove_tool(name)` | Deregister a tool |
| `tool_names` | `list[str]` of registered tool names |

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

## Agent Spawning by GOAT Supervisor

The GOAT supervisor spawns agents as templates based on task decomposition.
Each task in the DAG specifies a `role`, and the supervisor uses the
corresponding agent runner:

```python
# From supervisor/workflow.py
RUNNER_MAP = {
    "researcher": _run_researcher,
    "coder": _run_coder,
    "critic": _run_critic,
    "summarizer": _run_summarizer,
    "tool_caller": _run_tool_caller,
}
```

The supervisor calls `registry.settings.agents.get(role)` to get the ModelSpec
for each agent, then invokes the runner with the task and dependencies.

### Role Execution Patterns

| Role | Tool Access | Source | Description |
|------|-----------|-------|------------|
| `researcher` | web_search only | net | Deep research, forces web search |
| `coder` | FILE_TOOLS | file/net/generated | Code generation with file tools |
| `critic` | none | generated | Critical review |
| `summarizer` | none | generated | Synthesis |
| `tool_caller` | ALL_TOOLS | file/net/generated | General orchestration |
| `planner` | none | generated | Task decomposition |
| `memory` | memory tools | file/generated | Memory operations |

## config/agents.py Registry

The canonical agent role registry is in `config/agents.py`:

```python
from config.agents import (
    AGENT_ROLES,          # All valid roles
    EXECUTION_ROLES,     # Roles requiring tool calls
    SYNTHESIS_ROLES,     # Roles generating content
    DEFAULT_AGENT_ROLE, # Fallback role
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

## Import Examples

### Import agent classes

```python
from agents import (
    BaseAgent,
    CoderAgent,
    CriticAgent,
    PlannerAgent,
    ResearcherAgent,
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

## Model Override

Override model at construction:

```python
from config.settings import get_model

CoderAgent(spec=get_model("gpt-4o"))
```

## See Also

- `supervisor/workflow.py` — DAG execution engine
- `supervisor/dag_validator.py` — Result validation
- `config/agents.py` — Role registry
- `config/settings.py` — Model specifications