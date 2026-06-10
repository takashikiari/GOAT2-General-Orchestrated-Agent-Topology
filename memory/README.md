# Memory System — Three-Tier Architecture

## Overview

GOAT uses a three-tier memory system to separate short-term session context,
medium-term episodic history, and long-term persistent knowledge.

---

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

---

## Access Control

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

---

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

---

## Tool Access

### GOAT Memory Tools (16 tools — full tier access)

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
| `MEMORY_EXPORT` | Export tier data |
| `MEMORY_PROMOTE` | Promote between tiers |
| `MEMORY_AUTO_PROMOTE` | Auto-promote |

### DAG Memory Tools (4 tools — working tier only)

| Tool | Description |
|------|-------------|
| `memory_search` | Search working memory |
| `memory_get` | Get from working memory |
| `memory_store` | Store to working memory |
| `memory_recent` | Recent working memory entries |

---

## Implementation Details

### Storage Format

- **Working (Redis):** Key-value with TTL. Keys: `turn_<timestamp>_<role>`, `session:<id>:<field>`
- **Episodic (ChromaDB):** Vector embeddings with metadata. IDs: `turn_<YYYYMMDD_HHMMSS_uuuuuu>`
- **Long-term (Letta):** Structured passages with metadata. IDs: `passage-<uuid>` or `turn_<timestamp>`

### Role Tagging

- GOAT operations use `role="goat"` for memory writes
- DAG operations use `role="user_session"` for memory writes
- This separation allows filtering and provenance tracking
