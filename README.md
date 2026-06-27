# GOAT 2.0 — Memory Architecture

## Overview

GOAT 2.0 implements a **layered memory architecture** with three physical tiers and five logical layers. The system combines **proactive prefetch** with **on‑demand tool access**, giving GOAT the context it needs without forcing a fixed budget or classifier mechanics.

---

## Physical Tiers (Storage)

| Tier | Backend | Role |
|------|---------|------|
| **Permanent** | Letta | Identity, user profile, persona, long‑term facts, preferences |
| **Working** | Redis | Active conversation, current context, session state, L2.5 cache |
| **Episodic** | ChromaDB | Conversation history, documents, semantic retrieval |

All backends are **swapable** via the mapper layer — GOAT never accesses them directly.

---

## Logical Layers (What GOAT Sees)

| Layer | Content | Injection | Source |
|-------|---------|-----------|--------|
| **L0 — Identity** | Who GOAT is, rules, objectives | System prompt (always) | Permanent |
| **L1 — Critical Facts** | User profile, persona, preferences | System prompt (always) | Permanent |
| **L2 — Working Context** | Current conversation, recent messages | System prompt (always) | Working |
| **L2.5 — Session Cache** | Cached search results (TTL 2‑10 min) | System prompt (when available) | Working |
| **L3 — Episodic** | Historical conversations, documents | System prompt (prefetched or via tool) | Episodic |

GOAT only ever sees L0‑L3. It never knows about Redis, ChromaDB, or Letta.

---

## How Memory Works

### 1. Per‑Turn Prefetch (Async, Non‑Blocking)

Before GOAT responds, the orchestrator starts an **async prefetch**:

1. Estimates **confidence** and **complexity** from the user message.
2. If confidence is high, searches episodic memory (via L2.5 cache) for relevant historical context.
3. Runs **in parallel** with L0/L1/L2 loading — never blocks the response.
4. Uses **Adaptive Intent Token Scaling (AITS)** to allocate the right budget per intent.

If prefetch completes within the timeout (configurable), results are injected into the system prompt as a `[Related Memory]` block. If not, GOAT responds without it — the `search_memory` tool remains available as a fallback.

### 2. Adaptive Intent Token Scaling (AITS)

Fixed token budgets cause context loss (logs show 7352 tokens → 0). AITS replaces the fixed limit with a **dynamic** budget:

```

budget = base + (confidence × multiplier) + complexity_bonus
capped at BUDGET_HARD_CAP (configurable)

```

- **Confidence** — how certain GOAT is that this query needs deep context  
- **Complexity** — message length, entities, AND/OR operators  
- **Bonus** — additional tokens for complex messages or tool-heavy intents

L2 (live conversation) is **always protected** — L2 is never fully dropped. L3 (episodic) receives whatever budget remains after L2 is satisfied.

### 3. GOAT Decides Organically (No Classifier)

GOAT does **not** use a separate classifier, intent detector, or second model. It reads L0+L1+L2, then decides within the same LLM call:

- If something is missing, GOAT can call the `search_memory` tool (step 4).
- If something is worth preserving for future sessions, GOAT can call the `store_memory` tool (step 5).
- There is no external "intent" logic — GOAT is the sole decision‑maker.

### 4. Session Cache (L2.5)

Prevents duplicate searches:

- TTL: configurable (default 5 minutes)
- Cache key: `chat_id + query_hash`
- Cache hit → return stored results, skip ChromaDB
- Cache miss → perform search, store results

---

## Tools

| Tool | Purpose | Called By |
|------|---------|-----------|
| `search_memory` | Search episodic memory (L3) | GOAT, when needed |
| `store_memory` | Save important information to L3 | GOAT, when worth preserving |

Both tools are **organic** — GOAT decides when to call them, without external prompting or classifier logic.

---

## Project Structure (Memory‑Related)

```

goat2/
├── memory/
│   ├── layers.py            # Backend mapper (L0‑L3 → Permanent/Working/Episodic)
│   ├── aits.py              # Adaptive Intent Token Scaling
│   ├── budget.py            # Token estimation & enforcement
│   ├── session_cache.py     # L2.5 Session Cache (TTL)
│   ├── permanent/           # Letta backend
│   ├── working/             # Redis backend
│   └── episodic/            # ChromaDB backend
├── orchestrator/
│   └── orchestrator.py      # Async prefetch + context assembly
├── tools/
│   ├── memory_tools.py      # search_memory tool
│   └── memory_writer.py     # store_memory tool
└── config/
└── memory.toml          # Budget, cache, and timeout settings

```

---

## Configuration (`config/memory.toml`)

```toml
[aits]
budget_base = 2000
budget_confidence_multiplier = 4000
budget_complexity_max_bonus = 2000
budget_hard_cap = 12000
prefetch_timeout = 0.5
prefetch_confidence_threshold = 0.5

[cache]
ttl_seconds = 300

[retrieval]
max_results_per_search = 15
```

---

Key Design Decisions

Decision Why
Three physical tiers Resilience — if one backend fails, others still work
Five logical layers GOAT sees only the layers, never the backends
Async prefetch Context is available when needed, without blocking
AITS Fixed budgets don't scale — dynamic budgets do
No classifier GOAT decides organically, no second model, no "voices in the head"
L2 always protected Live conversation context is never fully dropped
search_memory fallback Even if prefetch fails, GOAT can retrieve manually

---

Testing

Test Result
Write → read across sessions ✅ store_memory → search_memory works
Long conversation (7352 tokens) ✅ budget scales, no dropped 1/1 blocks
Duplicate queries ✅ L2.5 cache hits, no duplicate ChromaDB calls
Prefetch timeout ✅ GOAT responds without context, fallback available
Multiple intents ✅ AITS allocates budget per intent

---

Future Work

· Predictive cache — L2.5 evolves to anticipate what GOAT might need
· Budget feedback loop — system learns from past over/under‑allocation
· Multi‑backend routing — choose which backend to query based on intent
