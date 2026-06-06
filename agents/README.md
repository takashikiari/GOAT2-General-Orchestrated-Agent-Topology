# agents/ — GOAT 2.0 Agent Layer

`BaseAgent` is the foundation. Subclass it, set `role`, implement `execute()`.
The instance is immediately usable as an `AgentRunner` (via `__call__`).

## Quick start

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

## `@tool` decorator

Marks a method as a callable tool. Auto-discovered at `__init__` and sent to the
LLM as an OpenAI function-calling schema. The `_chat` loop intercepts `tool_calls`,
dispatches them, appends results, and continues until the model returns plain text.

```python
@tool(name="validate_syntax", description="…", parameters={…})
async def _validate(self, code: str, language: str = "python") -> str: …
```

**Tool calling compatibility:** OpenAI ✓ · Groq llama-3.3-70b ✓ · DeepSeek chat/coder ✓  
DeepSeek reasoner (R1) ✗ — pass `tools=[]` to `_chat` (auto-suppressed in `ResearcherAgent`).

## Concrete agents

| Class | File | Model | Temp | Notes |
|-------|------|-------|------|-------|
| `PlannerAgent` | `planner.py` | gpt-4o | 0.3 | Structured plan output |
| `ResearcherAgent` | `researcher.py` | deepseek-reasoner | 0.3 | Auto-suppresses tools for R1 |
| `CoderAgent` | `coder.py` | deepseek-coder | 0.2 | `validate_syntax` tool |
| `CriticAgent` | `critic.py` | llama-3.3-70b | 0.3 | `extract_verdict()`, `is_blocking()` |

Override model at construction: `CoderAgent(spec=get_model("gpt-4o"))`.

## `BaseAgent` public surface

| Method | Purpose |
|--------|---------|
| `execute(task, context)` | **Abstract** — implement in subclass |
| `__call__(task, context)` | Delegates to `execute`; satisfies `AgentRunner` protocol |
| `_chat(messages, *, temperature, json_mode, max_tool_rounds, tools)` | Agentic tool-calling loop |
| `_build_messages(task, context)` | Composes `[system, user]` from context + task prompt |
| `add_tool(name, desc, params, handler)` | Register a tool at runtime |
| `remove_tool(name)` | Deregister a tool |
| `tool_names` | `list[str]` of registered tool names |
