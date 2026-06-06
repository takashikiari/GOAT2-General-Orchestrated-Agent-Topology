# GOAT 2.0 — Session Notes
**Date:** 2026-06-06  **Branch:** main (no git repo yet)

---

## What was done this session (patch 59)

### Memory pipeline redesigned — clear GOAT vs DAG separation

**Root cause:** Confusion between namespaces and roles — DAG and GOAT used different
_ROLE values, memory_recent searched wrong namespace, store_turn wrote to one namespace
but tools read another.

**New design:**
- **GOAT (supervisor)**: Direct memory_manager access to all 3 tiers
  - Uses role="goat" for its own context and state
  - Uses role="user_session" for session turns
  - Reads recent turns, session context, user profile directly — no tool calls needed
- **DAG (agents)**: Memory tools with tier="working" (Redis) only
  - Writes execution results to role="user_session" WORKING tier only
  - Reads ONLY from Redis (working memory) for current session context
  - Does NOT read from ChromaDB or Letta directly — GOAT handles long-term recall

**Fixes applied:**

**`supervisor/session.py`**:
- `store_turn` now writes to WORKING tier (Redis) ONLY with role="user_session"
- Removed EPISODIC and LONG_TERM writes — GOAT supervisor handles promotion via promote_turn()

**`tools/memory_temporal_tools.py`**:
- _ROLE = "user_session" (consistent with store_turn)
- memory_recent default tier="working" (Redis only for DAG agents)
- memory_timeline default tier="working" (Redis only for DAG agents)

**`supervisor/runner_memory.py`**:
- GOAT reads from all 3 tiers using role="goat" and role="user_session"
- Tier 1: WORKING (Redis) — current session turns
- Tier 2: EPISODIC (ChromaDB) — recent history
- Tier 3: LONG_TERM (Letta) — persistent rules with role="goat"

**`memory/memory_manager.py`**:
- Added `promote_turn(turn_key, content)` method
- Moves important turns from Redis (WORKING) to ChromaDB (EPISODIC) at session end

**`supervisor/supervisor.py`**:
- `finalize_session()` now calls promote_turn() before behavior analysis
- Promotes all session turns from Redis to ChromaDB for cross-session recall

All 37 tests pass. All files ≤200 lines with docstrings.

---

## What was done this session (patch 58)

### Planner memory tier mapping — redis/working memory queries use memory tools not file search

**Root cause:** When users asked about "redis", "working memory", "chromadb", or "letta",
the planner didn't know these refer to memory tiers and would spawn file search tasks
instead of memory tool calls.

**Fix applied:**

**`supervisor/planner.py`**:
- `PLANNER_SYSTEM` updated with explicit memory tier mappings:
  - "redis" or "working memory" → tool_caller with memory_recent(tier=working)
  - "chromadb" or "episodic memory" → tool_caller with memory_recent(tier=episodic)
  - "letta" or "long term memory" → tool_caller with memory_recent(tier=long_term)
- Added explicit rule: "Memory checks use memory tools (memory_recent, memory_search, memory_get)"
- Added explicit rule: "File search (file_search, file_read) is ONLY for workspace files, not memory"

**Key prefix verification:**
- Verified `memory/redis_conn.py` uses `goat2:working:` prefix in `_rkey()`
- Verified `memory/working_crud.py` passes `agent_role` namespace consistently to backend
- No prefix mismatch found — keys are consistent across write and read paths.

All 37 tests pass. File ≤200 lines with docstrings.

---

## What was done this session (patch 57)

### Letta human block garbage accumulation — strict ALLOWED_KEYS whitelist

**Root cause:** `info_extract.maybe_store_info` extracted arbitrary key-value pairs from
conversation messages and stored them all in the Letta `human` block. Over time this
accumulated hundreds of irrelevant facts (agent_id, passage_id, search_key, timestamps, etc.)
that corrupted planner context and caused spurious task triggering.

**Fix applied:**

**`supervisor/info_extract.py`**:
- Added `_ALLOWED_KEYS` frozenset whitelist: `name, age, location, city, language, workspace,
  gender, occupation, preferences, rules, canal, device, nationality`.
- Routing logic updated:
  - explicit + whitelisted → PollutionGuard → Letta human block
  - explicit + non-whitelisted → ChromaDB episodic with 7-day TTL
  - inferred + whitelisted → ChromaDB episodic with 7-day TTL
  - inferred + non-whitelisted → discarded entirely

**`memory/pollution_guard.py`**:
- Added same `_ALLOWED_KEYS` frozenset whitelist.
- `validate_fact()` now blocks any key not in ALLOWED_KEYS regardless of kind.

Both files ≤200 lines with docstrings. All 37 tests pass.

---

## What was done this session (patch 56)

### Planner re-triggering tasks from assistant DAG results

**Root cause:** `as_context()` in `supervisor/history.py` returned both user AND assistant
turns. Assistant turns contain DAG execution results (web search outputs, file reads, memory
queries). The planner saw these as new intent and spawned duplicate tasks.

**Fix applied:**

**`supervisor/history.py`**:
- `as_context()` now filters to user turns only (`m["role"] == "user"`).
- Added new method `as_full_context()` that returns all turns for display/memory only.
- `as_plan_context()` uses `as_context()` (user-only) for planning context.

**`supervisor/planner.py`**:
- `PLANNER_SYSTEM` updated with explicit rules:
  - "Decompose ONLY the current user intent. Ignore previous assistant responses."
  - "Do NOT use prior DAG results (web search, file reads) as input for new tasks."

All 37 tests pass. Both files ≤200 lines with docstrings.

---

## What was done this session (patch 55)

### Telegram token resolution — env var takes precedence over goat.toml

**`supervisor/interfaces/telegram_bot.py`**:
- `_TOKEN` now resolves via: `TELEGRAM_TOKEN` env var → `goat.toml` `[channels].telegram_token` → error.
- Uses `os.environ.get("TELEGRAM_TOKEN")` first, falls back to `load_toml().channel_str("telegram_token")`.
- `build_app()` raises `RuntimeError` with clear message if neither source provides a token.

All 37 existing tests pass. No imports broken. File remains ≤200 lines with docstrings.

---

## What was done this session (patch 54)

### Three hallucination-prevention fixes + response discipline

All modified files ≤200 lines. 37 tests pass. No imports broken.

**P1 — Empty output after file tool call (`tool_runner.py`)**
Already implemented in a previous patch (lines 92-100). Confirmed present — no change needed.

**P2 — GOAT hallucinates when lacking facts**
`supervisor/supervisor.py`:
- `_unverified_summary` now appends `via {tool_name}` to each failure entry when a tool
  was identified (net_error, empty_file_read cases).
- After `synthesize_results`: if summary is empty/whitespace, GOAT sets a factual fallback
  listing tools called — no LLM re-call.
`supervisor/runners.py`:
- `_run_summarizer`: guard added before the LLM call — if ALL dep_results have empty
  outputs, returns immediately without calling LLM.

**P3 — Response discipline at all times**
`supervisor/identity.py`: `GOAT_SYSTEM` updated to include `"no apologies"`.
`supervisor/critique.py`: synthesis prompt updated to include `"No apologies."`

---

## What was done this session (patch 53)

### Four Telegram / DAG safety fixes

All modified files ≤200 lines. New file (content_filter.py) ≤90 lines. 37 tests pass.

**P1 — Empty Telegram message**
`telegram_bot.py`: strip + validate `result.summary` before `reply_text`.

**P2 — Hallucination on missing file content**
`dag_validator.py`: added `_is_empty_file_read`.
`supervisor.py`: reason `"empty_file_read"` → specific summary.

**P3 — Sensitive content leaking to Telegram**
New `supervisor/interfaces/content_filter.py`: `mask_sensitive(text)` two-stage filter.

**P4 — Missing source label blocks DAG execution**
`task_prep.py`: `prepare_tasks` pre-sets `task.source = "planner"`.
`workflow.py`: source-related validation issues now raise `ValueError`.

---

## What was done this session (patch 52)

### DAG source enforcement — block generated on execution tasks

Five interlinked fixes applied to prevent DAG agents from hallucinating results under
`source='generated'`. All modified files ≤200 lines. 37 existing tests pass.

**Fixes applied:**

- `supervisor/types.py`: `AgentResult` gains `tool_called`, `tool_name`, `raw_output_hash`.
- `supervisor/workflow.py`: Populates new fields when building `AgentResult`.
- `supervisor/dag_validator.py`: Rewritten with `_EXECUTION_ROLES` and `_ROLE_ALLOWED_SOURCES`.
- `supervisor/runners.py`: `_run_researcher` and `_run_tool_caller` raise on source='generated'.
- `supervisor/runner_memory.py`: Tier 3 LLM distillation removed.
- `supervisor/supervisor.py`: Collects unsafe `val_statuses`, sets `summary = "Unverified"`.

---

## What works

### Infrastructure
- **Redis auto-detection** — `cli.py` pings Redis on startup; uses `RedisBackend` if up.
- **ChromaDB telemetry** — posthog noise suppressed at `CRITICAL` logger level.

### 3-layer memory (`memory/`)
- **Working** — `WorkingMemoryLayer` with `DictBackend` or `RedisBackend`.
- **Episodic** — `ChromaMemoryClient` (ChromaDB 1.1.1, cosine HNSW).
- **Long-term** — `LettaClient` → Letta 0.16.8 with graceful fallback.

### Supervisor (`supervisor/`)
- **Intent classifier** — `classify_intent()` via gpt-4o-mini.
- **Conversational** — `direct_response()` with GOAT identity + user profile.
- **Analytical** — planner gets `[Lightweight: ≤2 tasks, no researcher]` hint.
- **Complex** — full DAG: planner → wave execution → critique → synthesize.
- **Session persistence** — turns stored to WORKING, promoted to EPISODIC at session end.
- **User profile** — lazy-loaded from Letta `"human"` block on first `run()`.

### CLI (`cli.py`)
- Async chat loop, single `GoatSupervisor` instance across turns.
- `store_turn()` called after every successful run.

### Tools (`tools/`)
- 17 tool definitions with module-level docstrings.
- All file tools share `FileToolExecutor` security gateway.
- Memory tools default to tier="working" for DAG agents.

---

## Known limitations
- Letta long-term memory only works when the Letta server is running locally.
- Groq API key not configured — `summarizer` and `critic` default to gpt-4o-mini.
- No persistent git history yet; all changes tracked in `CHANGELOG.md`.
