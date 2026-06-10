# Memory System — Three-Tier Architecture

## Overview

GOAT uses a three-tier memory system to separate short-term session context,
medium-term episodic history, and long-term persistent knowledge.

## Directory Structure

```
memory/
├── __init__.py          # Re-exports for backward compatibility
├── config.py            # Memory-specific constants (moved from config/memory.py)
├── chromadb_client.py   # Backward compat shim → memory.episodic
├── letta_client.py     # Backward compat shim → memory.long_term
├── router/             # Memory routing and classification
├── working/             # Redis-backed session-scoped storage
│   ├── __init__.py
│   ├── working_memory.py   # Main working memory layer
│   ├── working_backend.py # StorageBackend Protocol
│   ├── redis_backend.py   # Redis implementation
│   ├── dict_backend.py    # In-memory dict implementation
│   ├── working_crud.py   # CRUD mixin
│   ├── working_query.py   # Query mixin
│   ├── working_search.py  # Search utilities
│   ├── working_sweep.py   # TTL eviction
│   ├── working_record.py # Record serialization
│   ├── redis_conn.py      # Redis connection
│   └── redis_scan.py      # Redis SCAN utilities
├── episodic/           # ChromaDB semantic storage
│   ├── __init__.py
│   ├── chromadb_client.py # Main ChromaDB client
│   ├── chromadb_base.py   # ChromaDB client management
│   ├── chroma_crud.py    # CRUD operations
│   ├── chroma_query.py    # Query operations
│   ├── chroma_extras.py  # Introspection
│   ├── chroma_helpers.py # Helper functions
│   ├── chroma_parsers.py # Result parsing
│   └── chroma_types.py   # Type definitions
├── long_term/          # Letta API integration
│   ├── __init__.py
│   ├── letta_client.py    # Main Letta client
│   ├── letta_blocks.py   # Core memory operations
│   ├── letta_health.py  # Health probing
│   ├── letta_helpers.py # Helper functions
│   ├── letta_registry.py # Agent registry
│   ├── letta_fallback.py # In-context fallback
│   └── letta_ops_*.py   # Letta operations
├── temporal/          # Time-based search
│   ├── __init__.py
│   ├── temporal_filter.py # Time filtering
│   ├── temporal_list.py  # Tier listing
│   ├── temporal_search.py # Temporal search
│   └── time_parser.py    # Time parsing
├── shared/            # Types and utilities
│   ├── __init__.py
│   ├── types.py           # Core types
│   ├── memory_enums.py    # Enumerations
│   ├── memory_manager.py # MemoryManager
│   ├── memory_crud.py   # CRUD mixin
│   ├── memory_search.py # Search mixin
│   ├── memory_promote.py # Promotion mixin
│   ├── hooks.py         # Auto-save hooks
│   ├── pollution_guard.py # Quality validation
│   └── validation.py    # Input validation
├── memory_tools/        # Tool definitions
│   ├── __init__.py
│   ├── memory_tools.py       # Core CRUD tools
│   ├── memory_helpers.py     # Shared utilities
│   ├── memory_temporal_tools.py
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
└── memory_metrics/     # Health metrics
    ├── __init__.py
    └── metrics.py         # Health monitoring functions
```

## Tiers

### 1. Working Memory — Redis (Short-term)

**Backend:** Redis
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

## Import Examples

### New Style (Recommended)

```python
# Import directly from subdirectories
from memory.working import WorkingMemoryLayer, RedisBackend
from memory.episodic import ChromaMemoryClient
from memory.long_term import LettaClient
from memory.shared import MemoryManager, MemoryEntry, MemoryType
from memory.temporal import filter_by_time, parse_time_range
```

### Old Style (Backward Compatible)

```python
# Import from memory module root
from memory import MemoryManager, MemoryEntry, WorkingMemoryLayer
from memory.chromadb_client import ChromaMemoryClient
from memory.letta_client import LettaClient
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

**Note:** `config/memory.py` is now a shim that re-exports from `memory.config` for backward compatibility.

## Memory Metrics

Health monitoring functions in `memory/memory_metrics`:

```python
from memory.memory_metrics import (
    count_working_entries,
    count_episodic_entries,
    count_long_term_entries,
    memory_health_report,
)

# Example usage
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

1. **Scrie în Redis** — comunică cu ceilalți agenți prin working memory
2. **Își ia context** din working memory pentru task-uri ample
3. **Nu are acces direct** la Episodic (ChromaDB) sau Long-term (Letta)
4. **Query către GOAT** — dacă are nevoie de informații din straturile profunde, face request către GOAT
5. **GOAT filtrează** — decide ce informații s�� returneze, cât, și dacă e relevant
6. **Zero halucinații** — memory agent nu primește niciodată date nevăzute sau nefiltrate

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
   ▼  (promoted by GOAT after session ends or when valuable)
Episodic (ChromaDB)
   │
   ▼  (promoted by GOAT for permanent knowledge)
Long-term (Letta)
```

## Tool Access

### GOAT Memory Tools (16 tools — full tier access)

Import from `memory.memory_tools`:

```python
from memory.memory_tools import (
    MEMORY_SEARCH,       # Semantic search across any tier
    MEMORY_GET,          # Get entry by exact key
    MEMORY_STORE,        # Store to any tier
    MEMORY_DELETE,      # Delete entry
    MEMORY_UPDATE,      # Update existing entry
    MEMORY_TIMELINE,    # Entries in time range
    MEMORY_RECENT,      # Most recent entries
    MEMORY_DEBUG_TRACE, # Per-tier debug info
    MEMORY_DIRECT_QUERY, # Raw queries to any backend
    MEMORY_LAST_WRITE,  # Last write timestamp
    MEMORY_COUNT,       # Entry count per tier
    MEMORY_TTL,         # TTL management
    MEMORY_EMBEDDING,   # Get embedding vector
    MEMORY_EXPORT,       # Export tier data
    MEMORY_PROMOTE,     # Promote between tiers
    MEMORY_AUTO_PROMOTE, # Auto-promote
)
```

### DAG Memory Tools (4 tools — working tier only)

| Tool | Description |
|------|-------------|
| `memory_search` | Search working memory |
| `memory_get` | Get from working memory |
| `memory_store` | Store to working memory |
| `memory_recent` | Recent working memory entries |

**Note:** Tools are also available from `tools.memory` (shim for backward compatibility).

## Implementation Details

### Storage Format

- **Working (Redis):** Key-value with TTL. Keys: `turn_<timestamp>_<role>`, `session:<id>:<field>`
- **Episodic (ChromaDB):** Vector embeddings with metadata. IDs: `turn_<YYYYMMDD_HHMMSS_uuuuuu>`
- **Long-term (Letta):** Structured passages with metadata. IDs: `passage-<uuid>` or `turn_<timestamp>`

### Role Tagging

- GOAT operations use `role="goat"` for memory writes
- DAG operations use `role="user_session"` for memory writes
- This separation allows filtering and provenance tracking