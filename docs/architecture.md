# GOAT Architecture

## System Overview

GOAT (General Orchestrator and Agent Taskmaster) is a multi-agent system with three-tier
persistent memory, intelligent intent routing, and a full tool-calling system.

### Core Principles

1. **Memory Separation** — Three tiers with strict access control
2. **Agent Isolation** — DAG agents have limited scope; GOAT supervises all
3. **Anti-Hallucination** — Data flows only through verified paths
4. **Source Provenance** — Every output tagged with its origin
5. **Pure LLM Routing** — Zero hardcoded keywords; the model decides
6. **DAG as Internal Thought** — Deep reasoning runs async; GOAT monitors from outside
7. **Working Memory as Nervous System** — GOAT↔DAG communicate only through Redis

---

## Intent Classification

GOAT 2.0 routes every user message through a **pure LLM classifier** —
no hardcoded keywords, no regex short-circuits, no greeting lists.
The classifier is the single decision point for whether a request
should be answered directly (CONVERSATIONAL), run through a small DAG
(ANALYTICAL), or trigger a full multi-agent pipeline (COMPLEX).

### `IntentDepth` — three routing depths

| Depth | Meaning | Path |
|---|---|---|
| `CONVERSATIONAL` | GOAT can answer directly | single LLM call with memory + web_search |
| `ANALYTICAL` | Lightweight DAG (≤2 tasks) | small DAG, no critic re-run |
| `COMPLEX` | Full DAG | planner → researcher/coder/critic → summarizer |

The enum is preserved exactly across all GOAT versions — callers and
validators that import `IntentDepth` are unaffected. The classifier
returns the enum directly.

### What the classifier LLM sees

The classifier is given a single prompt containing:

- **GOAT's direct capabilities** — what it can do without spawning
  the DAG (memory + web_search).
- **What requires the DAG** — multi-step research, code generation
  across multiple files, deep analysis, system configuration,
  architecture decisions.
- **Conversation history** — the last six user turns.
- **Active DAG sessions** — what's already in flight in working
  memory (so the LLM can prefer CONVERSATIONAL for follow-ups
  about in-flight work).
- **User profile** — the semantic summary from long-term memory
  (preferences, style, projects).
- **User override** — if the user explicitly asked for a specific
  routing mode (e.g. "answer directly" / "think deeply"), the
  override is applied unconditionally.
- **Prior corrections** — soft semantic hints from episodic memory:
  past corrections the user has made about similar intents.

The model replies with exactly one word: `conversational`,
`analytical`, or `complex`. On parse failure the classifier falls
back to CONVERSATIONAL (safe default — never escalate to a full
DAG on uncertainty).

### No hardcoded keywords

The classifier is **purely LLM-driven**. There are:

- ❌ no `re.compile(...)` patterns
- ❌ no `if "?" in text` checks
- ❌ no greeting lists
- ❌ no `help` detection
- ❌ no first-message length heuristics

If the user says "?", the LLM decides whether it's an inquiry or a
typo. If the user says "salut", the LLM decides whether it's a
greeting or a misspelled word in context. Every intent flows
through the same semantic path.

### Explicit user control

Users can route their message without typing "DAG" as a keyword:

- "Just answer me directly" / "nu rula DAG" / "tu singur" → force CONVERSATIONAL
- "Think about this deeply" / "pornește DAG" / "gândește profund" → force COMPLEX

Detection is **semantic**, not keyword-based. The override prompt
section describes in prose what an override looks like; the LLM
extracts the override semantically. The override is then stored in
working memory (`goat:<session_id>:override`, TTL = session
duration) so subsequent turns in the same session can re-use it
without re-asking the LLM.

### Behavioral learning via episodic memory

GOAT 2.0 learns **semantically** from the user's corrections. There
are zero hardcoded examples, zero ChromaDB seeding, and zero
"if the user said X, do Y" rules.

The learning loop:

1. **Detection** — when the user disagrees with GOAT's routing,
   the disagreement is detected semantically by the LLM (no
   keywords, no phrase matching).
2. **Storage** — the correction is written to **episodic memory**
   (ChromaDB) as a labeled example: original intent, what GOAT
   did, what the user wanted.
3. **Retrieval** — on the next similar intent, the classifier
   queries episodic memory for past corrections whose `intent` is
   semantically close. The LLM sees them as soft context.
4. **Adaptation** — the LLM uses the corrections as soft signals.
   Adaptability comes from semantic understanding, not pattern
   matching.

The user profile in long-term memory is a **semantic summary**
of the user, written by an LLM and updated as new signals arrive.
It is consulted by the classifier as plain prose.

---

## DAG as GOAT's Internal Thought Process

GOAT 2.0 is a two-layer system:

- **GOAT (supervisor)** — the interface with the user. It carries
  the conversation, holds the user profile, monitors background
  work, and synthesizes results. It has full access to all three
  memory tiers.

- **DAG (deep thinking)** — a multi-agent task pipeline that GOAT
  spawns in the background when the user's request is too complex
  for direct conversational handling. The DAG writes its progress
  and results to **working memory**; GOAT reads from there and
  never blocks on the DAG.

**The DAG is GOAT's internal thought process** — it runs
asynchronously, sends progress to a shared scratchpad (working
memory), and finishes by writing a final result. GOAT decides
**when** to think deeply; the LLM that powers GOAT makes this
decision based on the user's intent and what GOAT is currently
capable of doing directly.

### DAG progress reporting

`WorkflowGraph` writes a progress record to working memory after
every wave completes:

```python
key   = f"dag:{session_id}:progress"
value = {
  "wave": 3,
  "total_waves": 5,
  "completed_tasks": ["t1", "t2", "t4"],
  "status": "running",   # or "complete" on final wave
  "ts": <wall-clock>
}
ttl   = 3600
```

GOAT reads this on demand via the `query_dag_status` tool or
`memory_get` with the progress key. The progress key is
overwritten in place after each wave — no append-only log, no
versioning.

When the final wave finishes, the `status` is set to `"complete"`
and the same key is updated one last time before the final result
is written to `dag:<session_id>:result`.

### DAG awareness in GOAT

Before classifying a new intent, GOAT scans working memory for
active DAG sessions. The classifier LLM is given a summary of
what is in flight and is biased to prefer CONVERSATIONAL for
follow-up questions about in-flight work — so the user gets a
real-time progress report instead of a fresh DAG.

GOAT never runs tasks in parallel with the DAG. It monitors from
the outside, reads progress on demand, and synthesizes the final
result when the DAG finishes.

### Key namespaces

| Key pattern | Owner | Meaning |
|---|---|---|
| `goat:<session_id>:turn_<ts>` | GOAT | every turn (user + assistant summary) |
| `dag:<session_id>:progress`   | DAG  | current wave / total waves / status |
| `dag:<session_id>:result`     | DAG  | final result after all waves |
| `dag:<session_id>:task:<tid>` | DAG  | per-task intermediate output |
| `goat:<session_id>:override`  | GOAT | user override flag (force_conv / force_cplx) |

All keys are TTL-bound (default 3600s for DAG, 7200s for GOAT
turns). The nervous system is volatile by design.

---

## Working Memory as the Nervous System

Working memory (Redis) is the **nervous system** of GOAT 2.0. It
is the only channel through which GOAT and the DAG communicate.

### What GOAT writes

- `goat:<session_id>:turn_<ts>` — every turn (user + assistant summary), TTL 7200s.
- `goat:<session_id>:override` — explicit user override (force
  CONVERSATIONAL or COMPLEX), TTL 3600s.
- Intentions / task instructions to the DAG (read by DAG agents
  via the `dag:*:task:<tid>` namespace).

### What the DAG writes

- `dag:<session_id>:progress` — current wave / total / status,
  updated after every wave. TTL 3600s.
- `dag:<session_id>:task:<tid>` — per-task intermediate output,
  readable by downstream DAG agents via `memory_get`. TTL 3600s.
- `dag:<session_id>:result` — final result after all waves.
  TTL 3600s.

### What GOAT reads

- `dag:*:progress` — to report progress to the user and to feed
  the classifier LLM with "what is in flight" context.
- `dag:*:result` — to validate and synthesize the final answer.
- `dag:*:task:<tid>` — only via `validate_dag_result` in
  `goat_validator.py`, never as a tool call.

### What the DAG has zero access to

- ❌ Episodic (ChromaDB) — supervisor-only
- ❌ Long-term (Letta) — supervisor-only
- ❌ `goat:*` namespace — GOAT-owned, never written by the DAG

### Pollution guard

DAG execution data never pollutes episodic or long-term memory.
Progress reports, intermediate outputs, and final results stay
in working memory and expire with their TTL. The supervisor is
the only writer to episodic and long-term.

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
| File Tools | 8 | All agents (with shell restricted to DAG) |
| Web Search | 1 | All agents |
| Shell | 1 | DAG only (read-only) |
| System (think, calculator) | 2 | All agents |
| Memory Tools (GOAT) | 16 | GOAT only (full tier) |
| Memory Tools (DAG) | 4 | DAG only (working tier) |
| **Total** | **26 (in `ALL_TOOLS`)** | |

### Tool Distribution per Agent Role

The tool list per agent role is wired in `supervisor/pipeline/runners.py`
(per-DAG-agent) and `supervisor/identity.py` (GOAT conversational).
The table below mirrors the code exactly.

| Agent / Caller | File | Tools | Notes |
|---|---|---|---|
| **GOAT CONVERSATIONAL** | `supervisor/identity.py` | 16 memory + `WEB_SEARCH` | No file tools, no shell. Uses `registry.memory_tools`. |
| **file_op_result** (conversational file op) | `tools/file/file_op_response.py` | 10 `FILE_TOOLS` | Routes direct file requests through `tool_caller` model. |
| **DAG tool_caller** | `runners.py::_run_tool_caller` | 8 file + 4 DAG memory (12 total) | `tool_choice='required'` enforces tool invocation. |
| **DAG researcher** | `runners.py::_run_researcher` | `WEB_SEARCH, MEMORY_SEARCH_DAG` (2 total) | `tool_choice='required'`. Working tier only. |
| **DAG coder** | `runners.py::_run_coder` | 8 file + `SHELL` (9 total) | No web_search, no memory. Shell is read-only. |
| **DAG critic** | `runners.py::_run_critic` | `MEMORY_RECENT_DAG, MEMORY_GET_DAG` (2 total) | Working tier, read-only. |
| **DAG summarizer** | `runners.py::_run_summarizer` | `MEMORY_RECENT_DAG` (1 total) | Working tier, read-only. |
| **DAG memory** | `runners.py::_run_memory` | 4 DAG memory (4 total) | Working tier, restricted to `dag:*` namespace. |
| **DAG planner** | (no tools) | — | Pure LLM reasoning. |

**Working tier namespacing:**

- `dag:*` — DAG agents (`MEMORY_*_DAG` tools + `DAG_NAMESPACE`)
- `goat:*` — GOAT conversational (`MEMORY_TOOLS` + `GOAT_NAMESPACE`)
- `validator:*` — GOAT Validator (direct `memory_manager` access, no tool call)
- `promoter:*` — Memory Promoter (direct `memory_manager.promote()`, no tool call)

### routing + TYPE_CHECKING + Registry Applied to tools/

Every file in `tools/` follows the same architectural rules as `agents/`,
`supervisor/`, and `memory/`:

1. **`from __future__ import annotations`** at the top of every file
2. **`from typing import TYPE_CHECKING`** + `if TYPE_CHECKING:` for
   cross-module type hints (e.g. `ToolDefinition`, `MemoryManager`,
   `TaggedResult`)
3. **Lazy imports** inside function bodies for cross-module
   instantiation. The notable case is `tools/_make_tool.py::make_tool`
   which hides `from agents.base_agent import ToolDefinition` inside a
   function so the cross-layer import never appears in any tool
   module's top-level imports.
4. **No module-level singletons** — the file executor and web search
   are exposed as module-level `EXECUTOR` / `WEB_SEARCH` constants but
   are pure value objects, not stateful containers.
5. **Debug loggers** at namespace `goat2.tools.<submodule>` in every
   file, logging initialization + tool calls/params/results at DEBUG
   and errors / blocked ops / invalid params at WARNING.

### Debug Logger Namespace Tree (tools/)

```
goat2.tools                              — tools/__init__.py (top-level)
goat2.tools.make_tool                    — _make_tool.py
goat2.tools.tool_runner                  — tool_runner.py
goat2.tools.registry_accessor            — registry_accessor.py

goat2.tools.file                         — tools/file/__init__.py
goat2.tools.file.create                  — file_create.py
goat2.tools.file.grep                    — file_grep.py
goat2.tools.file.info                    — file_info.py
goat2.tools.file.list                    — file_list.py
goat2.tools.file.read                    — file_read.py
goat2.tools.file.read_lines              — file_read_lines.py
goat2.tools.file.search                  — file_search.py
goat2.tools.file.write                   — file_write.py
goat2.tools.file.op_response             — file_op_response.py
goat2.tools.file.executor                — file_executor.py
goat2.tools.file.executor_helpers        — file_executor_helpers.py
goat2.tools.file.storage                 — file_storage_service.py
goat2.tools.file.storage_helpers         — file_storage_helpers.py
goat2.tools.file.path_utils              — path_utils.py

goat2.tools.web                          — tools/web/__init__.py
goat2.tools.web.search                   — web_search.py

goat2.tools.system                       — tools/system/__init__.py
goat2.tools.system.calculator            — calculator.py
goat2.tools.system.think                 — think.py
goat2.tools.system.shell                 — shell_tool.py
```

**Log levels:**

- `DEBUG` — tool calls, parameters, results, search hits, dispatch info
- `INFO`  — successful file ops, list/read/write summaries
- `WARNING` — errors, blocked operations, invalid parameters, timeouts

**Enable verbose logging:**

```python
import logging
logging.basicConfig(level=logging.DEBUG)
logging.getLogger("goat2.tools").setLevel(logging.DEBUG)
```

### Circular-Import Fixes in tools/

Two pre-existing circular chains have been broken in tools/:

1. **`tools/file/file_op_response.py`** — imported
   `supervisor.types.Plan, SupervisorResult` at module level. Fixed by
   moving the import inside `file_op_result()` (lazy) and correcting
   the forward reference from the legacy `Registry` alias to the real
   class `ServiceRegistry`.

2. **`tools/tool_runner.py`** — imported
   `supervisor.logging.source_types` and
   `supervisor.logging.structured_logger` at module level, which
   transitively reached `supervisor.registry` →
   `supervisor.pipeline.runners` → back into `tools.tool_runner`.
   Fixed by moving the imports inside the body of `_call_with_tools()`
   (lazy). `TaggedResult` is still referenced in the return-type
   annotation, but it is a string under `from __future__ import
   annotations` and the real class is only resolved at call time.

The companion chain through `supervisor/pipeline/runners.py` (which
imports `from tools.tool_runner import _call_with_tools` at module
level) is the supervisor/ side of the same cycle; it is not modified
by this refactor because the tools/ side is now safe — importing
`tools/` does not pull in `supervisor/`.

### Verification

To prove the tools module is importable in isolation:

```python
import sys
import tools
# tools/ import must NOT pull in supervisor/ at module level
# (agents/ is pulled in via make_tool's function-local import — that is
#  the intended exception and is documented in tools/_make_tool.py)
print("ALL_TOOLS:", len(tools.ALL_TOOLS))                  # 26
print("FILE_TOOLS:", len(tools.FILE_TOOLS))                # 10
print("MEMORY_TOOLS:", len(tools.MEMORY_TOOLS))            # 16
print("DAG_MEMORY_TOOLS:", len(tools.DAG_MEMORY_TOOLS))    # 4

for t in tools.ALL_TOOLS:
    assert callable(t.handler), f"handler not callable: {t.name}"
    assert t.name, "missing name"
    assert t.description, "missing description"
    assert t.parameters, "missing parameters"
print("all 26 tools pass sanity check")
```

This passes because every cross-module dependency in `tools/` is
hidden behind a `TYPE_CHECKING` guard or a lazy import inside a
function body — the architectural rule "no agents/ or supervisor/
imports at module level in tools/" is enforced.

### Detailed Tool Reference

For the full per-tool description, parameter schema, and source file
of every `ToolDefinition`, see `tools/README.md` ("Tools Overview"
section). The bullet lists below summarise the catalog by tier.

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
| `supervisor/supervisor.py` | `goat2.supervisor` | intent routing, DAG execution, critic fallback |
| `supervisor/registry.py` | `goat2.supervisor.registry` | runner registered / looked up, init summary |
| `supervisor/identity.py` | `goat2.supervisor.identity` | profile load, direct response, onboarding |
| `supervisor/types.py` | `goat2.supervisor.types` | (type re-exports) |
| `supervisor/modul.py` | `goat2.supervisor.modul` | module registry operations |
| `supervisor/pipeline/*` | `goat2.supervisor.pipeline` | workflow waves, runner execution, validation |
| `supervisor/pipeline/dag.py` | `goat2.supervisor.pipeline.dag` | DAG cycle detection, topological sort |
| `supervisor/session/*` | `goat2.supervisor.session` | turn storage, history, memory injection |
| `supervisor/classification/*` | `goat2.supervisor.classification` | intent depth, language detect, direct bypass |
| `supervisor/logging/*` | `goat2.supervisor.logging` | audit, source types, structured logging |
| `supervisor/behavior/*` | `goat2.supervisor.behavior` | style analysis, mirroring, fact extraction |
| `supervisor/interfaces/*` | `goat2.supervisor.interfaces` | Telegram bot, content filter |

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

---

## Memory Architecture (Phase 5)

### Three-Tier Memory (Recap)

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
│  └──────────────────────────────────────────────────┘        │
└─────────────────────────────────────────────────────────────┘
```

### routing + TYPE_CHECKING + Registry Applied to memory/

Every file in `memory/` follows the same architectural rules as `agents/`,
`supervisor/`, and `tools/`:

1. **`from __future__ import annotations`** at the top of every file
2. **`from typing import TYPE_CHECKING`** + `if TYPE_CHECKING:` for
   cross-module type hints (e.g. `MemoryManager`, `ToolDefinition`,
   `AgentRegistry`)
3. **Lazy imports** inside function bodies for cross-module
   instantiation (e.g. `from memory.router import MemoryRouter` lives
   inside `MemoryManager._get_router()`)
4. **No module-level singletons** — `memory_manager`, `working_memory`,
   etc. have all been removed. Every consumer goes through
   `config/registry.py` `ServiceRegistry`.
5. **Debug loggers** at namespace `goat2.memory.<submodule>` in every
   file, logging initialization + read/write operations at DEBUG and
   errors / missing keys / tier unavailable at WARNING.

### Debug Logger Namespace Tree

```
goat2.memory                  — memory/__init__.py (top-level)
goat2.memory.config           — memory/config.py
goat2.memory.promoter         — memory/memory_promoter.py
goat2.memory.shared           — shared/types, enums, manager, hooks, validation
goat2.memory.working          — working/* (DictBackend, RedisBackend, sweep, query, …)
goat2.memory.chroma           — episodic/* (ChromaDB client, CRUD, query, parsers)
goat2.memory.letta            — long_term/* (Letta client, ops, registry, fallback)
goat2.memory.temporal         — temporal/* (filter, list, parser)
goat2.memory.router           — router/* (classifier, cache, executor, decision)
goat2.memory.tools            — memory_tools/* (all 16 tool handlers)
goat2.memory.metrics          — memory_metrics/* (counts, health)
```

**Log levels:**

- `DEBUG` — initialization, reads, writes, search hits, sweeps, routing
  decisions, layer timings, classifier scores
- `INFO` — promotion events, layer reconnection, service init
- `WARNING` — errors, missing keys, validation failures, tier unavailable,
  Redis corrupt records

**Enable verbose logging:**

```python
import logging
logging.basicConfig(level=logging.DEBUG)
logging.getLogger("goat2.memory").setLevel(logging.DEBUG)
```

### memory_promoter Pipeline

`memory/memory_promoter.py` is a pipeline component that handles automatic
tier promotion between memory tiers. It's distinct from
`memory.shared.hooks` (which does turn-based auto-save) and
`MemoryManager.promote_with_guard` (which does guarded promote).

**Promotion Rules (matching `MemoryManager.promote_turns`):**

| Condition | Promotion | keep_source |
|-----------|-----------|-------------|
| Turn 2+ (messages >= 4) | WORKING → EPISODIC | True |
| Turn 3+ (messages >= 6) | EPISODIC → LONG_TERM | False |

**Tool distribution note:**

- DAG agents: `FILE_TOOLS + WEB_SEARCH + DAG_MEMORY_TOOLS` (working tier)
- GOAT CONVERSATIONAL: `FILE_TOOLS + MEMORY_TOOLS` (all tiers)
- GOAT VALIDATOR: direct `memory_manager` access only
- GOAT Memory Promoter: direct `memory_manager.promote()` only

**Example:**

```python
from memory.memory_promoter import MemoryPromoter

promoter = MemoryPromoter(memory_manager)
if promoter.should_promote_to_episodic(turn_count):
    await promoter.promote_to_episodic(turn_count)
# Or auto-decide:
await promoter.promote_turn(turn_count)
```

### Verification

To prove the memory module is importable in isolation:

```python
import sys
import memory
import memory.shared, memory.working, memory.episodic
import memory.long_term, memory.temporal, memory.router
import memory.memory_metrics, memory.memory_promoter
# All of the above MUST succeed without importing agents/, supervisor/, or tools/
forbidden = [m for m in sys.modules if m.startswith(('agents.', 'supervisor.', 'tools.'))]
assert not forbidden, f"memory/ leaked: {forbidden}"
```

This passes because every cross-module dependency in `memory/` is hidden
behind a `TYPE_CHECKING` guard or a lazy import inside a function body.

---

## Circular Import Resolution Strategy

GOAT 2.0 has three import layers that must not import each other at module level:
`agents/`, `supervisor/`, `tools/`. Known cross-layer dependencies and their resolutions:

### supervisor/ → agents/ (resolved: lazy imports)

| File | Import | Resolution |
|---|---|---|
| `supervisor/supervisor.py` | `decompose_plan`, `critique_results`, `synthesize_results` | Lazy inside `run()` |
| `supervisor/supervisor.py` | `CriticVerdict` | `TYPE_CHECKING` block |
| `supervisor/registry.py` | `_run_planner` | Lazy inside `_register_defaults()` |
| `supervisor/__init__.py` | `critique_results`, `decompose_plan`, etc. | Backward-compat re-exports (allowed) |

### tools/ → supervisor/ (pre-existing, tolerated)

`tools/tool_runner.py` imports `supervisor.logging.source_types` at module level.
This works because `supervisor.logging.source_types` is a leaf module with no
transitive supervisor imports. The import is safe as long as `supervisor` is
initialized before `tools.file.file_op_response` is imported directly (which is
always the case in production: `from supervisor import ...` triggers initialization
of the full import chain first).

`tools/file/file_op_response.py` previously imported `supervisor.types` at module
level — this was fixed: `supervisor.types` and `supervisor.behavior.behavior_mirror`
are now lazy imports inside `file_op_result()`. Also fixed: the import path was
`supervisor.behavior_mirror` (wrong) → `supervisor.behavior.behavior_mirror` (correct).

### Verification

```python
import supervisor  # must initialize first in the app startup path
print('OK — no circular imports')

# Verify no module-level agents/ imports in supervisor/ (except __init__ compat re-exports)
import ast, pathlib
for f in pathlib.Path('supervisor').rglob('*.py'):
    if '__init__' in f.name:
        continue
    src = f.read_text()
    for node in ast.parse(src).body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in getattr(node, 'names', [])]
            mod = getattr(node, 'module', '') or ''
            if mod.startswith('agents.') or any(n.startswith('agents.') for n in names):
                print(f'VIOLATION: {f}: module-level agents/ import')
```

### Tool Imports — Important

`memory/__init__.py` deliberately does **NOT** re-export the `MEMORY_*`
tool definitions. Importing them transitively pulls
`tools → supervisor → tools` which is a pre-existing circular chain in
the codebase. Use `from memory.memory_tools import MEMORY_SEARCH` instead.

