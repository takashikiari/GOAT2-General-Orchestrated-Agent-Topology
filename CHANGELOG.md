# Changelog

All notable changes to GOAT 2.0 are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased] — 2026-06-06 (patch 59)

### Changed

#### Memory pipeline redesigned — clear GOAT vs DAG separation

**Root cause:** Confusion between namespaces and roles — DAG and GOAT used different
_ROLE values, memory_recent searched wrong namespace, store_turn wrote to one namespace
but tools read another.

**New design:**
- **GOAT (supervisor)**: Direct memory_manager access to all 3 tiers (WORKING/EPISODIC/LONG_TERM)
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
- Docstring updated to clarify DAG agents should not write to ChromaDB/Letta

**`tools/memory_temporal_tools.py`**:
- _ROLE = "user_session" (consistent with store_turn)
- memory_recent default tier="working" (Redis only for DAG agents)
- memory_timeline default tier="working" (Redis only for DAG agents)
- Docstrings clarify DAG vs GOAT usage patterns

**`supervisor/runner_memory.py`**:
- GOAT reads from all 3 tiers using role="goat" and role="user_session"
- Tier 1: WORKING (Redis) — current session turns
- Tier 2: EPISODIC (ChromaDB) — recent history
- Tier 3: LONG_TERM (Letta) — persistent rules with role="goat"
- Returns error with source="generated" if no memory found (triggers UNVERIFIED)

**`memory/memory_manager.py`**:
- Added `promote_turn(turn_key, content)` method
- Moves important turns from Redis (WORKING) to ChromaDB (EPISODIC) at session end
- Called by finalize_session() in supervisor

**`supervisor/supervisor.py`**:
- `finalize_session()` now calls promote_turn() before behavior analysis
- Promotes all session turns from Redis to ChromaDB for cross-session recall
- Docstring updated to document the 2-step process (promote + analyze)

All 37 tests pass. All files remain ≤200 lines with docstrings.

---

## [Unreleased] — 2026-06-06 (patch 58)

### Fixed

#### Planner memory tier mapping — redis/working memory queries use memory tools not file search

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
- These rules prevent the planner from confusing memory tier queries with file operations.

**Key prefix verification:**
- Verified `memory/redis_conn.py` uses `goat2:working:` prefix in `_rkey()`
- Verified `memory/working_crud.py` passes `agent_role` namespace consistently to backend
- Verified `supervisor/session.py` stores turns to WORKING tier (in addition to EPISODIC/LONG_TERM)
- No prefix mismatch found — keys are consistent across write and read paths.

All 37 tests pass. File remains ≤200 lines with docstrings.

---

## [Unreleased] — 2026-06-06 (patch 57)

### Fixed

#### Letta human block garbage accumulation — strict ALLOWED_KEYS whitelist

**Root cause:** `info_extract.maybe_store_info` extracted arbitrary key-value pairs from
conversation messages and stored them all in the Letta `human` block. Over time this
accumulated hundreds of irrelevant facts (agent_id, passage_id, search_key, timestamps,
etc.) that corrupted planner context and caused spurious task triggering.

**Fix applied:**

**`supervisor/info_extract.py`**:
- Added `_ALLOWED_KEYS` frozenset whitelist: `name, age, location, city, language, workspace,
  gender, occupation, preferences, rules, canal, device, nationality`.
- Routing logic updated:
  - explicit + whitelisted → PollutionGuard → Letta human block
  - explicit + non-whitelisted → ChromaDB episodic with 7-day TTL
  - inferred + whitelisted → ChromaDB episodic with 7-day TTL
  - inferred + non-whitelisted → discarded entirely
- `_merge()` function updated to filter by ALLOWED_KEYS before overlaying new pairs.
- New `_store_in_chroma()` helper for non-whitelisted explicit facts.

**`memory/pollution_guard.py`**:
- Added same `_ALLOWED_KEYS` frozenset whitelist.
- `validate_fact()` now blocks any key not in ALLOWED_KEYS regardless of kind (explicit/inferred).
- This provides defence-in-depth — even if info_extract sends a non-whitelisted key, guard blocks it.

Both files remain ≤200 lines with docstrings. All 37 tests pass.

---

## [Unreleased] — 2026-06-06 (patch 56)

### Fixed

#### Planner re-triggering tasks from assistant DAG results in conversation history

**`supervisor/history.py`**:
- `as_context()` now returns ONLY user turns, not assistant turns.
- Assistant turns contain DAG execution results (web search, file reads, memory queries) which
  should NOT influence planning — only user intent matters for task decomposition.
- Added new method `as_full_context()` that returns all turns (user + assistant) for
  display/memory purposes only. Documented that it must NOT be used for planning.
- `as_plan_context()` updated to use `as_context()` (user-only) instead of `as_full_context()`.

**`supervisor/planner.py`**:
- `PLANNER_SYSTEM` updated with explicit rules:
  - "Decompose ONLY the current user intent. Ignore previous assistant responses."
  - "Do NOT use prior DAG results (web search, file reads) as input for new tasks."
- These rules prevent the planner from spawning duplicate tasks when it sees assistant
  responses containing tool outputs in the conversation history.

All 37 existing tests pass. No imports broken. Both files remain ≤200 lines with docstrings.

---

## [Unreleased] — 2026-06-06 (patch 55)

### Fixed

#### Telegram token resolution — env var takes precedence over goat.toml

**`supervisor/interfaces/telegram_bot.py`**:
- `_TOKEN` now resolves via: `TELEGRAM_TOKEN` env var → `goat.toml` `[channels].telegram_token` → error.
- Uses `os.environ.get("TELEGRAM_TOKEN")` first, falls back to `load_toml().channel_str("telegram_token")`.
- `build_app()` raises `RuntimeError` with clear message if neither source provides a token.
- Module docstring updated to document the resolution order.
- This allows operators to keep `goat.toml` clean of secrets while using environment variables in production.

All 37 existing tests pass. No imports broken. File remains ≤200 lines with docstrings.

---

## [Unreleased] — 2026-06-06 (patch 54)

### Fixed

#### P1: Empty output after file tool call (tool_runner.py — already applied, confirmed)
`_call_with_tools` no-tool-calls return path already falls back to the last `role=tool`
history entry when `msg.content` is empty. No code change needed — confirmed present.

#### P2: GOAT hallucinates when it lacks facts

**`supervisor/supervisor.py`**:
- `_unverified_summary` now includes the tool that was called (when available) in each
  failure line: `"researcher via web_search: web search returned an error"` instead of
  `"researcher: web search returned an error"`. Uses `AgentResult.tool_name`.
- After `synthesize_results`, if the returned summary is empty or whitespace, GOAT now
  sets `summary` to a factual fallback listing the tools that were called:
  `"Not available. Tools called: {tools}. No output from synthesis."` — no LLM call.
  Prevents silent empty responses reaching the interface.

**`supervisor/runners.py`**:
- `_run_summarizer`: added pre-check — if all upstream `dep_results` have empty outputs,
  the LLM is never called. Returns `"Not available. Upstream tasks returned no output."`
  immediately. Removes the fallback that previously called the LLM with empty context
  and could generate plausible-sounding but unverified content.

#### P3: Supervisor response discipline — explicit at all times

**`supervisor/identity.py`**:
- `GOAT_SYSTEM`: added `"no apologies"` to the no-filler rule so the constraint is
  unambiguous: `"No filler, no preamble, no apologies, no sign-offs."` Previously only
  `"no filler"` was listed, leaving apologies uncovered.

**`supervisor/critique.py`**:
- `synthesize_results` system prompt: added `"No apologies."` alongside the existing
  `"No headers, no tables, no preamble labels. No questions at the end."` rule.
  Synthesis LLM now has explicit guidance not to apologise for missing data.

All 37 existing tests pass. No imports broken. All modified files ≤200 lines with
docstrings on every function.
