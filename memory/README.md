# Memory System — Three-Tier Architecture

## Overview

GOAT 2.0 uses a three-tier memory system to separate short-term session context,
medium-term episodic history, and long-term persistent knowledge. This README
documents the **routing + TYPE_CHECKING + Registry** architecture that has been
applied across every file in `memory/`.

## Architecture Principles

1. **Zero singletons** — No module-level instances. All access goes through
   `config/registry.py` (`ServiceRegistry.memory_manager`).
2. **Zero circular imports** — `agents/`, `supervisor/`, and `tools/` never
   touch each other at module level. `memory/` keeps its internal imports
   tree-shaped via `TYPE_CHECKING` blocks.
3. **`TYPE_CHECKING` for cross-module types** — When `memory/` files reference
   types from `agents/`, `supervisor/`, or `tools/`, the import is guarded by
   `if TYPE_CHECKING:` so it never executes at runtime.
4. **Lazy instantiation** — `MemoryRouter`, `MemoryPromoter`, and any
   cross-module instantiations happen *inside* functions, not at module load.
5. **Debug loggers everywhere** — Every file owns a
   `logging.getLogger("goat2.memory.<submodule>")` and emits structured DEBUG
   / WARNING logs for visibility.

## Directory Structure

```
memory/
├── __init__.py              # Re-exports (NO tool re-exports — avoid circular import)
├── config.py                # Memory-specific constants
├── chromadb_client.py       # Backward-compat shim → memory.episodic
├── letta_client.py          # Backward-compat shim → memory.long_term
├── memory_promoter.py       # Automatic tier promotion
├── working/                 # Redis-backed session-scoped storage
│   ├── __init__.py
│   ├── working_memory.py    # Main WorkingMemoryLayer
│   ├── working_backend.py   # StorageBackend Protocol
│   ├── redis_backend.py     # Redis implementation
│   ├── dict_backend.py      # In-memory implementation
│   ├── working_crud.py      # store/retrieve/delete/clear/health
│   ├── working_query.py     # search/list/ttl_of/count
│   ├── working_search.py    # _tokenize, _score (pure)
│   ├── working_sweep.py     # TTL eviction
│   ├── working_record.py    # RecordDict + record conversion
│   ├── redis_conn.py        # Async Redis client lifecycle
│   └── redis_scan.py        # SCAN-based bulk ops
├── episodic/                # ChromaDB semantic storage
│   ├── __init__.py
│   ├── chromadb_client.py   # Main ChromaDB client
│   ├── chromadb_base.py     # Client + collection management
│   ├── chroma_crud.py       # store/retrieve/delete
│   ├── chroma_query.py      # search/list/clear/health
│   ├── chroma_extras.py     # count/collections (introspection)
│   ├── chroma_helpers.py    # ID/keys/metadata (pure)
│   ├── chroma_parsers.py    # get/query result parsers
│   └── chroma_types.py      # TypedDict definitions
├── long_term/               # Letta API integration
│   ├── __init__.py
│   ├── letta_client.py      # Main LettaClient
│   ├── letta_blocks.py      # Core memory get/set
│   ├── letta_health.py      # HTTP client + liveness
│   ├── letta_helpers.py     # Pure parsing helpers
│   ├── letta_registry.py    # Per-role agent ID cache
│   ├── letta_fallback.py    # Pure in-memory fallback
│   ├── letta_ops_retrieve.py
│   ├── letta_ops_list.py
│   └── letta_ops_store.py
├── temporal/                # Time-based search
│   ├── __init__.py
│   ├── temporal_filter.py   # filter_by_time, resolve_range
│   ├── temporal_list.py     # gather_tier_list (fan-out list)
│   ├── temporal_search.py   # timeline / recent / debug_trace
│   └── time_parser.py       # Natural-language time parsing
├── shared/                  # Types, enums, and utilities
│   ├── __init__.py
│   ├── types.py             # Core types (NewType wrappers, Protocol)
│   ├── memory_enums.py      # MemoryType, LayerStatus
│   ├── memory_manager.py    # MemoryManager (mixin composition)
│   ├── memory_crud.py       # store/retrieve/delete/clear
│   ├── memory_search.py     # search (single-tier + fan-out)
│   ├── memory_promote.py    # promote() / promote_all()
│   ├── memory_promote_turns.py # promote_turns() (turn-based, extracted)
│   ├── hooks.py             # auto_save_memory
│   ├── pollution_guard.py   # Fact quality validation
│   └── validation.py        # Key/value sanitization
├── router/                  # Intelligent memory router
│   ├── __init__.py
│   ├── router.py            # MemoryRouter
│   ├── types.py             # RoutingDecision, LayerTiming
│   ├── cache.py             # RouteCache (LRU)
│   ├── classifier.py        # classify_query
│   ├── confidence.py        # compute_confidence
│   ├── decision.py          # make_decision
│   ├── executor.py          # execute_route
│   ├── layer_stats.py       # LayerStats, LayerStatsTracker
│   └── preferences.py       # preferred_layers
├── memory_tools/            # Tool definitions (handlers)
│   ├── __init__.py
│   ├── memory_tools.py      # GOAT: SEARCH/GET/STORE
│   ├── memory_tools_dag.py  # DAG: SEARCH/GET/STORE (working only)
│   ├── memory_helpers.py    # Shared utilities
│   ├── memory_temporal_tools.py  # TIMELINE/RECENT
│   ├── memory_debug_trace_tool.py # DEBUG_TRACE
│   ├── memory_delete_tool.py
│   ├── memory_direct_query.py
│   ├── memory_count_tool.py
│   ├── memory_update_tool.py
│   ├── memory_promote_tool.py
│   ├── memory_auto_promote_tool.py
│   ├── memory_embedding_tool.py
│   ├── memory_export_tool.py
│   ├── memory_last_write.py
│   └── memory_ttl_tool.py
└── memory_metrics/          # Health metrics
    ├── __init__.py
    └── metrics.py           # count_*/memory_health_report
```

## Tier Architecture

### 1. Working Memory — Redis (Short-term)

**Backend:** Redis (DictBackend in tests)
**Purpose:** Session context, active conversation, tool output, DAG bridge
**TTL:** Configurable (default: 1 hour)
**Access:** GOAT (full) + DAG agents (Redis only)

**Used for:**
- Current conversation turns
- Tool call outputs during a session
- Communication bridge between DAG agents
- Context for task execution

**DAG agents** read and write here exclusively.

### 2. Episodic Memory — ChromaDB (Medium-term)

**Backend:** ChromaDB
**Purpose:** Past conversations, session histories, behavioral patterns
**TTL:** Persistent (no auto-expiry)
**Access:** GOAT only — DAG agents have NO direct access

**Used for:**
- Previous session summaries
- User behavior patterns
- Historical context for personalization
- Learning from past interactions

### 3. Long-term Memory — Letta (Permanent)

**Backend:** Letta
**Purpose:** User preferences, profiles, long-term knowledge, core memories
**TTL:** Permanent
**Access:** GOAT only — DAG agents have NO direct access

**Used for:**
- User identity and preferences
- Long-term knowledge graph
- Core memories that persist across all sessions
- Promoted important episodic memories

## Debug Logger Namespaces

Every file in `memory/` owns a debug logger. The full tree is:

```
goat2.memory                 — top-level (memory/__init__.py)
goat2.memory.config          — memory/config.py
goat2.memory.promoter        — memory/memory_promoter.py
goat2.memory.shared          — shared/types, enums, manager, hooks, validation
goat2.memory.working         — working/* (DictBackend, RedisBackend, sweep, …)
goat2.memory.chroma          — episodic/* (ChromaDB client, CRUD, query)
goat2.memory.letta           — long_term/* (Letta client, ops, fallback)
goat2.memory.temporal        — temporal/* (filter, list, parser)
goat2.memory.router          — router/* (classifier, cache, executor)
goat2.memory.tools           — memory_tools/* (all 16 tool handlers)
goat2.memory.metrics         — memory_metrics/* (counts, health)
```

**Log levels:**
- `DEBUG` — initialization, reads, writes, search hits, sweeps, routing decisions
- `WARNING` — errors, missing keys, validation failures, tier unavailable

**Enable verbose logging:**

```python
import logging
logging.basicConfig(level=logging.DEBUG)
logging.getLogger("goat2.memory").setLevel(logging.DEBUG)
```

## Import Examples

### New Style (Recommended)

```python
# Import directly from subdirectories
from memory.working import WorkingMemoryLayer, RedisBackend
from memory.episodic import ChromaMemoryClient
from memory.long_term import LettaClient
from memory.shared import MemoryManager, MemoryEntry, MemoryType
from memory.temporal import filter_by_time, parse_time_range
from memory.router import MemoryRouter, classify_query
from memory.memory_metrics import memory_health_report
```

### Old Style (Backward Compatible)

```python
# Import from memory module root — works for non-tool symbols
from memory import MemoryManager, MemoryEntry, WorkingMemoryLayer
from memory import MemoryRouter
from memory.chromadb_client import ChromaMemoryClient
from memory.letta_client import LettaClient
```

### Tool Imports (MUST use memory.memory_tools)

```python
# IMPORTANT: do NOT import tools from `memory` directly — that triggers a
# tools → supervisor → tools circular import. Always use memory.memory_tools:
from memory.memory_tools import (
    MEMORY_SEARCH, MEMORY_GET, MEMORY_STORE, MEMORY_DELETE,
    MEMORY_UPDATE, MEMORY_TIMELINE, MEMORY_RECENT, MEMORY_DEBUG_TRACE,
    MEMORY_DIRECT_QUERY, MEMORY_LAST_WRITE, MEMORY_COUNT, MEMORY_TTL,
    MEMORY_EMBEDDING, MEMORY_EXPORT, MEMORY_PROMOTE, MEMORY_AUTO_PROMOTE,
    MEMORY_SEARCH_DAG, MEMORY_GET_DAG, MEMORY_STORE_DAG, MEMORY_RECENT_DAG,
)
```

## Configuration

Memory configuration constants are in `memory/config.py`:

```python
from memory.config import (
    WORKING_BACKEND,         # "redis"
    EPISODIC_BACKEND,       # "chromadb"
    LONG_TERM_BACKEND,     # "letta"
    PROMOTION_TURN_EPISODIC,  # 2
    PROMOTION_TURN_LONG_TERM,  # 3
    POLLUTION_GUARD_MIN_LENGTH, # 10
)
```

`config/memory.py` is a shim that re-exports from `memory.config` for
backward compatibility.

## Memory Metrics

Health monitoring functions in `memory/memory_metrics`:

```python
from memory.memory_metrics import (
    count_working_entries,
    count_episodic_entries,
    count_long_term_entries,
    memory_health_report,
)

report = await memory_health_report(mm)
# Returns: {"status": {"working": True, "episodic": True, "long_term": False},
#          "counts": {"working": 50, "episodic": 100, "long_term": 0},
#          "healthy": True}
```

## Access Control

| Actor | Working (Redis) | Episodic (ChromaDB) | Long-term (Letta) |
|-------|----------------|---------------------|-------------------|
| **GOAT** | ✅ Full R/W | ✅ Full R/W | ✅ Full R/W |
| **DAG Agents** | ✅ Redis only | ❌ | ❌ |
| **Memory Agent** | ✅ Redis (bridge) | ❌ (query via GOAT) | ❌ (query via GOAT) |

### Memory Agent — Redis Bridge

Memory agent is a special DAG agent:

1. **Writes to Redis** — communicates with other agents through working memory
2. **Pulls context** from working memory for complex tasks
3. **No direct access** to Episodic (ChromaDB) or Long-term (Letta)
4. **Query to GOAT** — if it needs deeper-tier data, makes a request to GOAT
5. **GOAT filters** — decides what to return, how much, and whether relevant
6. **Zero hallucinations** — memory agent never receives unfiltered or unseen data

## Data Flow

```
1. User sends message
2. GOAT routes to DAG (if complex) or handles directly (if simple)
3. DAG agents work → store results in Redis (working)
4. GOAT reads results from Redis
5. GOAT may promote relevant info:
   Working → Episodic (ChromaDB) → Long-term (Letta)
6. GOAT responds to user
```

### Promotion Flow

```
Working (Redis)
   │
   ▼  (MemoryPromoter.promote_to_episodic at turn 2+)
Episodic (ChromaDB)
   │
   ▼  (MemoryPromoter.promote_to_longterm at turn 3+)
Long-term (Letta)
```

**Turn thresholds (in `memory_promoter.py`):**

- `EPISODIC_THRESHOLD = 4` — messages >= 4 (turn 2+)
- `LONG_TERM_THRESHOLD = 6` — messages >= 6 (turn 3+)

## Tool Access

### GOAT Memory Tools (16 tools — full tier access)

Import from `memory.memory_tools`:

| Tool | Description |
|------|-------------|
| `MEMORY_SEARCH` | Semantic search across any tier |
| `MEMORY_GET` | Get entry by exact key |
| `MEMORY_STORE` | Store to any tier |
| `MEMORY_DELETE` | Delete entry |
| `MEMORY_UPDATE` | Update existing entry |
| `MEMORY_TIMELINE` | Entries in time range |
| `MEMORY_RECENT` | Most recent entries |
| `MEMORY_DEBUG_TRACE` | Per-tier debug info |
| `MEMORY_DIRECT_QUERY` | Raw queries to any backend |
| `MEMORY_LAST_WRITE` | Last write timestamp |
| `MEMORY_COUNT` | Entry count per tier |
| `MEMORY_TTL` | TTL management |
| `MEMORY_EMBEDDING` | Get embedding vector |
| `MEMORY_EXPORT` | Bulk export as JSON |
| `MEMORY_PROMOTE` | Promote between tiers |
| `MEMORY_AUTO_PROMOTE` | Bulk promotion |

### DAG Memory Tools (4 tools — working tier only)

| Tool | Description |
|------|-------------|
| `MEMORY_SEARCH_DAG` | Search working memory |
| `MEMORY_GET_DAG` | Get from working memory |
| `MEMORY_STORE_DAG` | Store to working memory |
| `MEMORY_RECENT_DAG` | Recent working memory entries |

**Note:** DAG tool handlers live in `memory_tools_dag.py`; GOAT tools in
`memory_tools.py`; debug-trace in `memory_debug_trace_tool.py`; timeline +
recent in `memory_temporal_tools.py`. This split keeps every file under the
260-line ceiling.

## Implementation Details

### Storage Format

- **Working (Redis):** Key-value with TTL. Keys: `goat2:working:{role}:{key}`
- **Episodic (ChromaDB):** Vector embeddings with metadata. IDs: `goat2_{role}_{key}`
- **Long-term (Letta):** Structured passages with metadata. Passage text
  prefixed with `[KEY:{key}]\n{content}` so retrieve() can recover the key.

### Role Tagging

- GOAT operations use `role="goat"` for memory writes
- DAG operations use `role="user_session"` for memory writes
- This separation allows filtering and provenance tracking

## TYPE_CHECKING + Routing Pattern

Cross-module type hints follow this pattern:

```python
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from memory.shared.memory_manager import MemoryManager
    from agents.base_agent import ToolDefinition
    from supervisor.registry import AgentRegistry

# Runtime: never imports MemoryManager at module load — only when the
# annotated function is actually called.
```

Lazy instantiation of cross-module classes:

```python
async def some_handler(...):
    if cls is None:
        from memory.shared.memory_manager import MemoryManager
        cls = MemoryManager  # resolved only when handler runs
```

The `ServiceRegistry` from `config/registry.py` is the single source of truth
for all service instances — no module-level singletons anywhere.
