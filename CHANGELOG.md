# Changelog

All notable changes to GOAT 2.0 are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased] — 2026-07-07 (session 6)

### Architectural redesign: post-turn prefetch

**Problem**: Prefetch ran at START of turn competing with a 1.5s timeout → always timed out on cold turns (ChromaDB + GLiNER + CrossEncoder ≈ 1.5-2.5s) → orchestrator fell back to `search_memory` tool → 2 LLM calls per turn → 10s latency. The timeout was the wrong fix for the wrong problem.

**Root cause (architectural inversion)**: Prefetch is called *prefetch* because it pre-fetches for the NEXT turn. Running it at the start of turn N and waiting for it with a timeout is the opposite of pre-fetching — it's synchronous retrieval with a bad timeout.

**Fix**: Prefetch now runs POST-TURN as a fire-and-forget background task, in the inter-turn gap while the user reads the reply. No timeout needed — it completes before the user sends the next message. The orchestrator reads pre-computed L3 from activation (L2.5) instantly; no search pipeline runs during the turn at all.

**Files changed**:
- `orchestrator/prefetch.py` — complete rewrite: `run_prefetch_and_save()` replaces `run_prefetch` + `save_prefetch_background`. No timeout parameter. Runs post-turn.
- `orchestrator/orchestrator.py` — `run()` restructured:
  - Removed: start-of-turn prefetch task, `asyncio.wait` with PREFETCH_TIMEOUT, timeout handling, `current_activation`, `update_activation` inline call
  - Added: instant L3 read from activation (warm/drift turns only; cold → empty), concurrent L0/L1/L2 fetch via `asyncio.gather`, post-turn `run_prefetch_and_save` fire-and-forget
  - Added: `current_topic_id` pre-computed at classify-time so `_archive_turn` and post-turn prefetch use the same consistent value (fixes topic_id="" bug on first turn)
  - Removed imports: `PREFETCH_TIMEOUT`, `update_activation`, `run_prefetch`, `save_prefetch_background`
  - Added imports: `rescore_recency`, `run_prefetch_and_save`
- `orchestrator/activation_manager.py` — `update_activation` gains `forced_topic_id` parameter so orchestrator can inject the pre-computed topic_id
- `tests/test_orchestrator_memory_flow.py` — updated `test_search_runs_unconditionally_and_reports_cache_key` to match new architecture (no synchronous search, post-turn prefetch, no cache_key during turn)

**Result**: Every turn is now 1 LLM call. No timeout. L3 context arrives via activation read (instant) on warm/drift turns. Cold turns serve empty L3 (the post-turn prefetch rebuilds activation for the next turn). `search_memory` remains available as an explicit on-demand tool, not as a timeout fallback.

---

## [Unreleased] — 2026-07-07 (session 5)

### Refactored

- **L3 retrieval extracted to `memory/retrieval.py`** (`orchestrator/prefetch.py` → `memory/retrieval.py`): The retrieval pipeline (search → merge → boost_by_entities → rerank) lived inside `orchestrator/prefetch.py`, which meant it was duplicated whenever a second caller (search_memory tool, on-demand retrieval) would need the same logic. Extracted into a first-class `retrieve()` function in the `memory` package — where retrieval belongs. `orchestrator/prefetch.py` is now a thin scheduling wrapper (~35 lines) that delegates to `memory.retrieval.retrieve`; orchestrator API unchanged.

### Fixed

- **Cold-path prefetch systematic timeout** (`config/memory.toml`, `memory/config_defaults.py`): Even with JIT-primed models (session 4 fix), the cold-path pipeline (ChromaDB × 3 parallel + BM25 + GLiNER boost + CrossEncoder rerank) runs sequentially and totals ~1.08s on a warm CPU — 8ms over the 1.0s timeout. Every cold turn (thread_break=true, activation_state=cold) timed out even after models were warm. Increased `PREFETCH_TIMEOUT` from 1.0s to 1.5s; warm/drift paths still complete in <0.8s and are unaffected.

---

## [Unreleased] — 2026-07-07 (session 4)

### Fixed

- **GLiNER double-load race condition** (`memory/gliner_extractor.py`): `_get_shared_model()` had no thread lock. Because `asyncio.to_thread` runs in a thread pool, two concurrent callers (e.g., warmup + an early prefetch) could both evaluate `_gliner_model is None` as `True` while `GLiNER.from_pretrained()` was still executing (~10 s), causing both to load the model independently — logging "GLiNERExtractor: model loaded" twice and wasting memory/CPU. Fixed with `threading.Lock()` + double-checked locking (outer check avoids lock contention on the hot path; inner check inside the lock prevents the race).

- **PyTorch JIT first-inference overhead causing prefetch timeout** (`memory/gliner_extractor.py`, `memory/reranker.py`): `warmup()` on both models only loaded weights into memory via `from_pretrained()` / `CrossEncoder()`. PyTorch JIT compiles the computation graph on the *first inference call*, not on model load. This caused:
  - CrossEncoder: ~0.96 s first prediction (1.04 it/s) vs ~0.11 s on subsequent calls (8.91 it/s) — enough to exceed the 1.0 s prefetch timeout even with the model fully in memory.
  - GLiNER: same JIT overhead on `predict_entities()`, adding latency inside the prefetch task.
  - Fixed by adding `_load_and_prime()` to both classes: loads the model then immediately runs a dummy inference (`predict_entities("warmup", ...)` / `model.predict([("warmup query", "warmup document")])`), compiling the JIT graph at startup so the first real prefetch call is as fast as subsequent ones.

---

## [Unreleased] — 2026-07-07 (session 3)

### Fixed

- **GLiNER first-turn prefetch timeout** (`memory/gliner_extractor.py`, `telegram_interface/_plugin_scanner.py`): `boost_by_entities` is called inside every cold/drift prefetch task, which calls `GLiNERExtractor.extract()`. GLiNER had no startup warmup, so the first cold/drift query triggered model load (~1-3 s from disk) inside the 1.0 s prefetch timeout — timeout was almost certain on the first message after every restart. Fixed by:
  - Adding `GLiNERExtractor.warmup()` (mirrors `CrossEncoderReranker.warmup()` — `asyncio.to_thread(_get_model)`).
  - Adding `registry.gliner_extractor.warmup()` to the parallel warmup block in `post_init_hook`, alongside BM25 and CrossEncoder. GLiNER now loads before the first message arrives.

- **Silent warmup failure swallowing** (`telegram_interface/_plugin_scanner.py`): `asyncio.gather(*warmup_tasks, return_exceptions=True)` caught all warmup exceptions without logging them, so a failed CrossEncoder or BM25 warmup was invisible in logs. Replaced with explicit per-component `log.error(...)` so operators know which component failed and that the first turn may be slow.

- **Misleading docstring in `entity_boost.py`**: The module docstring claimed GLiNER is "already warm from enrichment" — factually wrong on the first query (before any auto_promote enrichment has ever run). Updated to "pre-warmed at startup".

---

## [Unreleased] — 2026-07-07 (session 2)

### Added

- **`memory/config_defaults.py`**: extracted `_DEFAULTS` dict from `memory/config.py` so config.py stays within the 260-line code limit. Single source of truth for all TOML fallback values.

- **`memory/config_validator.py`**: fail-fast startup validation for `memory.toml`. Called by `config._load()` when a TOML file is present. Checks key invariants (`drift_warm > drift_cold`, `budget_base > 0`, `budget_hard_cap >= budget_base`, `l3_reserve_fraction ∈ (0,1)`, `timeout > 0`, `max_messages > 0`, etc.) and raises `ValueError` with a clear message pointing to the broken key — misconfiguration now fails at startup instead of silently producing wrong runtime behaviour.

- **`memory/context_assembler.py`**: extracted pure assembly logic from `MemoryLayers` into a standalone module. Contains `assemble_blocks` (the full L0–L3 block builder) plus `trim_recent_messages`, `fit_search_results`, `gap_filter`, `blended_gap_filter`, `format_facts`, `format_messages`. No I/O, no state, fully testable in isolation. `MemoryLayers.assemble_context` is now a thin wrapper that fetches missing inputs and delegates.

### Fixed

- **Cache staleness after L3 enrichment** (`memory/auto_promote.py`, `memory/layers.py`): `pair_and_enrich_dropped` was updating ChromaDB metadata but not invalidating the L2.5 SessionCache, so the next search on the same query returned stale pre-enrichment results from Redis. Fixed by:
  - Changing `create_task(pair_and_enrich_dropped(...))` → `await` inside `maybe_auto_promote` so enrichment completes inline (also makes it correctly testable via `asyncio.run()`).
  - Adding optional `cache_clear_fn` parameter to `maybe_auto_promote` / `schedule_auto_promote`; called after enrichment completes.
  - `MemoryLayers.append_and_save_working_context` passes `cache_clear_fn=lambda: self._cache.clear(chat_id)`.

- **Test regressions** (`tests/test_auto_promote.py`, `tests/test_auto_promote_enrichment.py`): two tests assumed any surplus > 0 triggers trim, conflicting with the `PROMOTE_MIN_SURPLUS = 4` threshold added in session 1. Updated to use surplus ≥ `PROMOTE_MIN_SURPLUS`; added explicit `test_below_min_surplus_is_noop` to document the threshold invariant. Enrichment tests now also correctly verify both dropped l3_ids are enriched.

- **Dead config** (`memory/config.py`, `config/memory.toml`): removed `L3_SIMILARITY_MAX_DISTANCE` constant (superseded by `l3_gap_significance`). Removed deprecated `l3_similarity_max_distance = 1.20` entry from TOML.

### Refactored

- **Fire-and-forget task tracking** (`orchestrator/orchestrator.py`, `memory/layers.py`, `telegram_interface/bot.py`):
  - `Orchestrator._pending_archives` → `_pending_bg`; `drain_archives()` → `drain_background()`. The prefetch background-save task (`save_prefetch_background`) is now also tracked in `_pending_bg` alongside archive tasks.
  - `MemoryLayers` gains `_pending_bg: set[asyncio.Task]` tracking auto_promote tasks + `drain_background(timeout)` method.
  - `schedule_auto_promote` now returns `asyncio.Task` so callers can track it.
  - Bot's `post_shutdown` hook calls both `orchestrator.drain_background()` and `layers.drain_background()`.

---

## [Unreleased] — 2026-07-07 (session 1)

### Fixed

- **GLiNER truncation** (`memory/gliner_extractor.py`): long texts (>200 words) were silently truncated to 384 tokens by the model. Added `_chunk_text` that splits into ≤200-word chunks, runs NER on each, and merges results with deduplication. Entities from the full text are now extracted correctly.

- **Prefetch timeout loop** (`orchestrator/orchestrator.py`, `orchestrator/prefetch.py`): `asyncio.wait_for` was cancelling the prefetch task on timeout, leaving the activation unsaved and forcing every subsequent turn cold. Replaced with `asyncio.wait` (which never cancels tasks) + `save_prefetch_background` that awaits the still-running task and saves activation so the next turn is warm.

- **auto_promote every-turn ping-pong** (`memory/auto_promote.py`): at steady state (conversation at cap) every turn added 2 messages then immediately dropped 2, running GLiNER enrichment on every single turn. Added `PROMOTE_MIN_SURPLUS = 4` threshold — trim only fires when surplus ≥ 4, halving enrichment frequency with negligible working-memory overhead.

### Refactored

- **Prefetch extracted from orchestrator** (`orchestrator/prefetch.py`, `orchestrator/activation_manager.py`): the prefetch daemon (`_prefetch_daemon`), background save (`_save_prefetch_background`), and activation persistence (`_update_activation`) were private methods of `Orchestrator`. Extracted to two single-responsibility modules:
  - `orchestrator/prefetch.py` — `run_prefetch` (warm/drift/cold search logic) + `save_prefetch_background`
  - `orchestrator/activation_manager.py` — `update_activation`
  - `Orchestrator` now calls these as plain functions, passing `layers` explicitly — no singletons, no registry coupling, no lazy imports.

---

## [0.1.1] — prior

See git log for earlier changes.
