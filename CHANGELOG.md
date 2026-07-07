# Changelog

All notable changes to GOAT 2.0 are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [0.1.3] — 2026-07-07

### Added

- **`memory/temporal_route.py`** — `parse_interval(entities, entity_types) -> tuple[float, float] | None`. Converts GLiNER-extracted date/time entity text to a Unix timestamp window without any external parser. Regex matches `d+ month_ro [year] [HH:MM]`; Romanian month lookup covers full names and abbreviations. Window: ±1 h when time is present, ±12 h (full day) for date-only. Future-date guard: if the parsed date is > 1 day ahead of `time.time()`, retries with `year - 1`. Fallback: when GLiNER labels a date as `"event"` rather than `"date"`, the regex still fires by scanning all entity texts.

- **`memory/temporal_route.py` — routing signal.** `parse_interval` checks `entity_types` for `"date"` / `"time"` first (O(n) pass); only scans all entity texts when no typed match exists. This avoids false positives from non-date entities.

- **`tests/test_temporal_route.py`** — 13 tests covering: full and abbreviated Romanian month names, date-only vs date+time windowing, explicit year, invalid day (32), empty entity list, "event"-labelled date fallback, non-date entity list (no match), separate date+time entities, midnight, future-year rollback. All deterministic via monkeypatched `datetime.now()` and `time.time()`.

### Changed

- **`memory/gliner_extractor.py`** — `_ENTITY_LABELS` gains `"date"` and `"time"`. GLiNER is zero-shot so adding labels is the only change needed; the model recognises these in Romanian text.

- **`memory/retrieval.py`** — GLiNER entity extraction now runs **inside the first `asyncio.gather`** alongside MiniLM and BM25 (`layers.extract_query_entities(query)` added to the gather in both `_drift` and `_cold`). After the gather, `_temporal_candidates()` checks `parse_interval` and fires `search_episodic(after=, before=)` when an interval is found. Temporal results join the candidate pool before CrossEncoder reranks.

  **Before:** candidate pool = [MiniLM, BM25]. CrossEncoder never received date-keyed entries for temporal queries.  
  **After:** candidate pool = [MiniLM, BM25, temporal (when applicable)]. CrossEncoder resolves "4 iulie 07:00" ↔ "2026-07-04 07:23" correctly.

  Also: `pre_extracted` entities passed to `boost_by_entities` — second GLiNER call eliminated.

- **`memory/entity_boost.py`** — `entity_boost` gains `pre_extracted: dict | None = None`. When provided by `retrieval.py`, the `await extractor.extract(query)` call is skipped (~0.5 s saved per turn on any query where GLiNER was already run for routing).

- **`memory/layers.py`** — two additions:
  - `extract_query_entities(query: str) -> dict`: thin wrapper over `self._extractor.extract`, returns `{"entities":[], "entity_types":[], "memory_type":"conversation"}` when extractor unavailable.
  - `boost_by_entities` gains `pre_extracted: dict | None = None` and passes it through to `entity_boost`.

---

## [0.1.2] — 2026-07-07

### Added

- **`memory/retrieval.py`** — canonical L3 retrieval pipeline (`retrieve()`, `_cold()`, `_topic_search()`). Extracted from `orchestrator/prefetch.py` so the prefetch daemon and `search_memory` tool share the same pipeline without code duplication. Single-responsibility: search → merge → boost_by_entities → rerank, no scheduling logic.

- **`memory/config_defaults.py`** — extracted `_DEFAULTS` dict from `memory/config.py`. Single source of truth for all TOML fallback values; keeps `config.py` within the line limit.

- **`memory/config_validator.py`** — fail-fast startup validation for `memory.toml`. Checks key invariants (`drift_warm > drift_cold`, `budget_base > 0`, `budget_hard_cap >= budget_base`, `l3_reserve_fraction ∈ (0,1)`, `max_messages > 0`, etc.) and raises `ValueError` with a clear message — misconfiguration now fails at startup instead of producing silent wrong runtime behaviour.

- **`memory/context_assembler.py`** — extracted pure assembly logic from `MemoryLayers` (`assemble_blocks`, `trim_recent_messages`, `fit_search_results`, `gap_filter`, `blended_gap_filter`, `format_facts`, `format_messages`). No I/O, no state, fully testable in isolation.

### Changed

- **Post-turn prefetch (architectural redesign)** (`orchestrator/prefetch.py`, `orchestrator/orchestrator.py`, `orchestrator/activation_manager.py`):

  **Problem**: Prefetch ran at the START of every turn under a `PREFETCH_TIMEOUT` → always timed out on cold turns (ChromaDB + GLiNER + CrossEncoder ≈ 1.5–2.5s) → orchestrator fell back to `search_memory` tool → 2 LLM calls per turn → ~10s latency. Increasing the timeout (1.0s → 1.5s) was the wrong fix for the wrong problem.

  **Root cause (architectural inversion)**: "Prefetch" means pre-fetch for the NEXT turn. Running it at the start of turn N and blocking on a timeout is synchronous retrieval disguised as prefetch.

  **Fix**: `run_prefetch_and_save()` now fires **post-turn** as a fire-and-forget `asyncio.Task`, in the inter-turn gap while the user reads the reply. No timeout. The orchestrator reads pre-computed L3 from activation (L2.5) instantly; no search pipeline runs during a turn.

  **Turn-time flow after this change:**
  1. `asyncio.gather(get_activation, embed_query)` — instant
  2. Classify turn state; compute `topic_return_id` + `current_topic_id`
  3. Serve L3 from `activation.merged` (0 ms) on warm/drift; empty on cold
  4. `asyncio.gather(get_identity_and_facts, get_identity_prompt, get_working_context)`
  5. Assemble → LLM → save
  6. `asyncio.create_task(run_prefetch_and_save(...))` — post-turn, tracked in `_pending_bg`

  **Removed**: `PREFETCH_TIMEOUT`, `asyncio.wait` timeout gate, `current_activation`, inline `update_activation` call, `run_prefetch`, `save_prefetch_background`.

  **`current_topic_id` pre-computation**: `topic_id` is now derived at classify-time and passed consistently to `_archive_turn` (so archive always has a non-empty UUID even on the first turn) and to `run_prefetch_and_save` via `forced_topic_id`. `update_activation` gains `forced_topic_id` parameter.

  **Result**: Every turn is 1 LLM call. `search_memory` remains available as an explicit on-demand tool, not a timeout fallback.

- **`orchestrator/prefetch.py`** — complete rewrite. `run_prefetch_and_save(layers, chat_id, intent, query_emb, turn_state, activation, topic_return_id, forced_topic_id)` → calls `retrieve()` then `update_activation()`. No timeout. ~40 lines.

- **`orchestrator/activation_manager.py`** — `update_activation` gains `forced_topic_id: str | None = None`. When provided, the pre-computed `topic_id` overrides the auto-generated UUID, ensuring archive and prefetch always agree on the current topic.

### Fixed

- **GLiNER double-load race condition** (`memory/gliner_extractor.py`): `_get_shared_model()` had no thread lock. Two concurrent callers (warmup + early prefetch) could both evaluate `_gliner_model is None` as `True` while `GLiNER.from_pretrained()` was executing (~10s), causing both to load independently — wasting memory/CPU and logging "GLiNERExtractor: model loaded" twice. Fixed with module-level `threading.Lock()` + double-checked locking.

- **PyTorch JIT first-inference overhead** (`memory/gliner_extractor.py`, `memory/reranker.py`): `warmup()` loaded model weights but PyTorch JIT compiles the computation graph on the *first inference call*, not on load. CrossEncoder: ~0.96s first prediction vs ~0.11s subsequent. GLiNER: same overhead. Fixed by adding `_load_and_prime()` to both: loads model then immediately runs a dummy inference, compiling the JIT graph at startup.

- **GLiNER startup warmup missing** (`telegram_interface/_plugin_scanner.py`): `boost_by_entities` called `GLiNERExtractor.extract()` on the first cold/drift prefetch, triggering model load (~1–3s from disk) inside the prefetch window. Added `GLiNERExtractor.warmup()` and added it to the parallel warmup block in `post_init_hook`.

- **Silent warmup failure swallowing** (`telegram_interface/_plugin_scanner.py`): `asyncio.gather(*warmup_tasks, return_exceptions=True)` caught all warmup exceptions without logging. Replaced with explicit per-component `log.error(...)`.

- **Cache staleness after L3 enrichment** (`memory/auto_promote.py`, `memory/layers.py`): `pair_and_enrich_dropped` updated ChromaDB metadata but didn't invalidate the L2.5 SessionCache. The next search on the same query returned stale pre-enrichment results from Redis. Fixed: `maybe_auto_promote` now accepts `cache_clear_fn`; `append_and_save_working_context` passes `lambda: self._cache.clear(chat_id)`.

- **GLiNER truncation** (`memory/gliner_extractor.py`): long texts (>200 words) were silently truncated to 384 tokens by the model. Added `_chunk_text` that splits into ≤200-word chunks, runs NER on each, and merges results with deduplication.

- **Prefetch timeout cancelling task** (`orchestrator/orchestrator.py`): `asyncio.wait_for` was cancelling the prefetch task on timeout, leaving the activation unsaved and forcing every subsequent turn cold. Replaced with `asyncio.wait` + `save_prefetch_background` that awaits the still-running task. (Superseded entirely by the post-turn prefetch redesign above.)

- **auto_promote every-turn ping-pong** (`memory/auto_promote.py`): at steady state, every turn added 2 messages then immediately dropped 2, running GLiNER enrichment every single turn. Added `PROMOTE_MIN_SURPLUS = 4` threshold — trim fires only when surplus ≥ 4.

- **Dead config key** (`memory/config.py`, `config/memory.toml`): removed `L3_SIMILARITY_MAX_DISTANCE` (superseded by `l3_gap_significance`).

- **Test regressions** (`tests/test_auto_promote.py`, `tests/test_auto_promote_enrichment.py`): updated to use `PROMOTE_MIN_SURPLUS` threshold. Updated `test_search_runs_unconditionally_and_reports_cache_key` to match the new post-turn prefetch architecture.

### Refactored

- **Fire-and-forget task tracking** (`orchestrator/orchestrator.py`, `memory/layers.py`, `telegram_interface/bot.py`): `_pending_archives` → `_pending_bg`; `drain_archives()` → `drain_background()`. All background tasks (archive, auto_promote, post-turn prefetch) tracked in `_pending_bg`. Bot's `post_shutdown` drains cleanly.

- **Prefetch extracted from orchestrator** (session 1): `_prefetch_daemon`, `_save_prefetch_background`, and `_update_activation` were private `Orchestrator` methods. Extracted to `orchestrator/prefetch.py` and `orchestrator/activation_manager.py` — plain functions, `layers` passed explicitly, no singletons.

---

## [0.1.1] — prior

See git log for earlier changes.
