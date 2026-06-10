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
