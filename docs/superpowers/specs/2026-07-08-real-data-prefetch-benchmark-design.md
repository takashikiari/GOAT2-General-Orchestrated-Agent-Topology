# Real-Data Prefetch Benchmark — Design

**Date:** 2026-07-08
**Status:** Approved (pending user spec review)
**Scope:** New benchmark tooling to evaluate the RRF-based prefetch pipeline (`memory/retrieval.py::retrieve`, `memory/result_merger.py`) end-to-end, on real (not synthetic) episodic memory content, without touching the live ChromaDB collection.

---

## 1. Goal

The RRF fusion prefetch pipeline (replacing the old blended-score fusion, see commit `86edfd2`) has never been exercised by the existing benchmark suite (`benchmark/`). All 16 built-in datasets test either:
- the `search_memory` tool path (`layers.search_episodic()` — plain semantic search, no BM25/RRF/rerank), or
- a single-turn `orchestrator.run()` call, where `activation` is always `None` (no prior turn wrote it), so the passive warm-serving path (`activation.merged` → `context_assembler`) never engages.

Neither path touches `memory/retrieval.py::retrieve()`, the module the prefetch daemon (`orchestrator/prefetch.py`) actually calls.

This design adds a benchmark layer that:
1. Measures **retrieval quality** of the RRF pipeline directly (hit@K, winning mechanism, `blended_score`, rank) — including the `drift`-state "prediction" candidate group.
2. Measures **full-cycle behavior** (prefetch daemon + orchestrator + LLM) in both **warm** and **cold** turn states, on the same query, to compare LLM output and hallucination rate across the two.
3. Uses **real, unedited content already stored in the live episodic ChromaDB collection** as source data — not synthetic distractor facts — so difficulty reflects actual conversation messiness (mixed-language, ambiguous, multi-topic), not hand-tuned distractor sets.
4. Never writes to, or concurrently reads at production-risk from, the live collection the running bot serves.

Out of scope: touching or refactoring the existing 16 synthetic datasets; building a general-purpose "offline LLM eval" framework; changing the RRF pipeline itself.

## 2. Constraints

- **No contamination of live data.** The running bot (`python3 -m telegram_interface`) must never see benchmark-seeded content in its retrieval results, and no benchmark write may land in the real collection under a real chat's data.
- **No artificial simplification.** Source content is used verbatim from the snapshot — no cherry-picking only "clean" entries, no rewriting for clarity. Difficulty must be real, not synthetic.
- **Ground truth generated in-pipeline.** No separate "offline" judge model — dataset-build-time LLM calls reuse `registry.llm_client` (the same configured production model).
- **Reuse, don't fork, the existing evaluator/runner where the shape fits** (`benchmark/evaluator.py`, `BenchmarkMetrics`, the `python -m benchmark` CLI) — new capability is additive.
- **File-size discipline** — new modules stay single-responsibility; split rather than grow `benchmark/datasets.py` (already 1251 lines) further.

## 3. Architecture

```
one-time / on-refresh:
  scripts/snapshot_episodic_for_benchmark.py
    live ChromaDB (read-only) → chroma_data_benchmark/ (verbatim copy)

  benchmark/real_data_mining.py
    scan chroma_data_benchmark/ → pick information-dense entries
    → registry.llm_client generates (query, expected_fact) per entry
    → cache to benchmark/data/real_recall_cases.json

per benchmark run:
  benchmark/prefetch_bench.py          (retrieval-only, no LLM)
    memory.retrieval.retrieve() against chroma_data_benchmark/
    → hit@K, mechanism, blended_score, rank

  BenchmarkRunner.run_conversation()   (full cycle, real LLM calls)
    turn 1 (real preceding context) → orchestrator.run()
    drain_background()                → prefetch daemon writes activation
    turn 2 (generated recall query)  → orchestrator.run()   [WARM]
    same turn 2 query, fresh chat_id → orchestrator.run()   [COLD]
    → Evaluator.groundedness_judge(response, retrieved_context) per turn
```

A dedicated `ServiceRegistry` is built pointing its `EpisodicMemory`/`BM25Index` at `chroma_data_benchmark/` (a distinct ChromaDB `PersistentClient` path — physically separate from the live `chroma_data/`). Redis-backed tiers (`WorkingMemory`, `SessionCache`, `ActivationStore`) are reused as-is: they are already namespaced by `chat_id`, and benchmark runs use fresh UUID chat_ids, so no collision risk with real chats.

## 4. Components

### 4.1 `scripts/snapshot_episodic_for_benchmark.py`
Read-only against the live collection. Exports every `(id, document, metadata)` row (same `col.get()` pattern as `repair_episodic.py`) and writes them verbatim into a new `PersistentClient` at a separate path (`chroma_data_benchmark/`, configurable). Idempotent — safe to re-run to refresh the snapshot; drops and recreates the benchmark collection each time. No connection to the live collection is ever opened for writing.

### 4.2 `benchmark/real_data_mining.py`
- **Candidate selection**: reads the snapshot, filters to information-dense entries — heuristic on word count plus (when present) the `importance` metadata written by `memory/enrichment.py::compute_importance`. Excludes generic short chit-chat (`"GOAT"`, `"Ce faci nebunule?"` style).
- **Ground-truth generation**: for each candidate, one call to `registry.llm_client` with a fixed prompt asking for (a) a natural follow-up recall question a real user might ask later, and (b) the short exact fact string an answer must contain. Structured output (JSON) parsed the same way other LLM tool outputs are parsed in this codebase.
- **Caching**: results written to `benchmark/data/real_recall_cases.json` (gitignored — derived from private conversation content, not committed). Regenerated only on demand, not on every benchmark run.
- Each generated case also carries the entry's own `chat_id` and, where identifiable, the immediately preceding entry from the same original conversation (for turn-1 lead-in context in the full-cycle test).

### 4.3 `benchmark/prefetch_bench.py`
Retrieval-only harness, no LLM call — the `debug_prefetch.py` instrumentation formalized as a reusable module:
- Runs `memory.retrieval.retrieve()` in `cold`, `warm`, and `drift` states against `chroma_data_benchmark/` for each mined case.
- `drift` state includes the "prediction" candidate group (results carried over from a prior activation, rescored by the cross-encoder) — reported as its own mechanism tag, same as `semantic_global`/`semantic_chat_scoped`/`bm25`/`temporal`.
- Per case, per state: was the ground-truth entry retrieved at all (by `message_id`) within `PREFETCH_MAX_RESULTS`, at what rank, with what `blended_score`, found by which mechanism(s).
- Reports aggregate hit@K and mean rank per mechanism per state — this is the "scoruri reale pe retrieval/prefetch/predicție" deliverable.

### 4.4 `BenchmarkRunner.run_conversation()` (new method, `benchmark/runner.py`)
- Accepts a mined case: `{chat_id_source, lead_in_turns, query, expected_fact}`.
- **Warm path**: fresh benchmark `chat_id`; preloads `lead_in_turns` (if available) or the target entry into L3 via `layers.store_episodic`; runs `orchestrator.run()` once (this also fires the post-turn prefetch daemon); calls `await orchestrator.drain_background()` to deterministically wait for the daemon to populate activation; runs `orchestrator.run(query, chat_id)` a second time and captures the response plus whatever `context_blocks`/retrieved content fed that turn (via the existing `ObservationCollector`/`memory_analytics` diff, extended per §4.5).
- **Cold path**: same query, brand-new `chat_id`, no lead-in, no drain — single `orchestrator.run()` call. No activation exists, so this exercises the cold-turn behavior (no passive L3 context; the LLM may or may not call `search_memory`).
- **Timing/reliability variant** (goal 3): same as warm path but replaces `drain_background()` with `asyncio.sleep(delay)` for a swept set of `delay` values, recording whether the daemon finished in time (activation populated) before the next turn — this measures the real race window instead of asserting it away.
- Returns per-turn: response text, `warm_served`, mechanism breakdown, latency, and the groundedness judge verdict.

### 4.5 Analytics/observability extension
`memory/observability.py` / `observability_collector.py` already carry `warm_served`, `prefetch_thematic_count` per turn but the benchmark's `_snapshot`/`_diff` in `runner.py` currently discards them. Extend `_snapshot`/`_diff` to also surface `warm_served` and the raw `context_blocks` (or at minimum the L3 block text) so the groundedness judge has the actual retrieved content to compare against — not a reconstruction.

### 4.6 `Evaluator.groundedness_judge` (new, `benchmark/evaluator.py`)
Given `(response, retrieved_l3_block)`, one `registry.llm_client` call asking: does the response make any claim not supported by the retrieved content, and — separately — did the response answer confidently despite the retrieved content being empty/irrelevant (a fabrication-on-empty-context case)? Returns `{grounded: bool, hallucinated_claims: list[str], answered_without_evidence: bool}`. This is independent of the `expected_fact` correctness check — a response can be "correct" by fuzzy-match and still contain an unsupported extra claim, and vice versa.

## 5. Data flow summary

| Stage | Reads | Writes | LLM calls |
|---|---|---|---|
| Snapshot | live ChromaDB | `chroma_data_benchmark/` | none |
| Mining | `chroma_data_benchmark/` | `benchmark/data/real_recall_cases.json` | 1 per candidate (one-time, cached) |
| `prefetch_bench.py` | `chroma_data_benchmark/` | none | none |
| `run_conversation` (warm) | `chroma_data_benchmark/`, Redis (bench chat_id) | L3/L2/activation under bench chat_id only | 2 (turn 1 + turn 2) + 1 groundedness judge per turn |
| `run_conversation` (cold) | `chroma_data_benchmark/`, Redis (bench chat_id) | L3/L2 under bench chat_id only | 1 + 1 groundedness judge |

## 6. Error handling

- Snapshot script aborts (non-zero exit) if exported row count doesn't match the live collection's `count()` at export time — mirrors `repair_episodic.py`'s existing safety check.
- Mining step: an LLM call that fails or returns unparseable JSON for a candidate skips that candidate (logged), never aborts the batch.
- `run_conversation`: an `orchestrator.run()` exception on either turn is caught and recorded as an error result (matches existing `run_single` behavior) — one failed case never aborts a dataset run.
- Groundedness judge failures degrade to `grounded: None` (unknown) rather than raising, consistent with `Evaluator.llm_judge`'s existing failure handling.

## 7. Testing

- Unit tests for `real_data_mining.py`'s candidate-selection heuristic (pure function, fake entries) and for the snapshot script's row-count safety check (fake/mock ChromaDB client, mirrors existing `test_activation.py`-style pure-logic tests).
- `prefetch_bench.py` gets a smoke test against a tiny fake snapshot (few entries) verifying hit@K accounting logic, independent of real data.
- `run_conversation` warm/cold paths get integration-style tests using the existing `tests/_orch_fakes.py` fakes, verifying `drain_background()` is actually awaited before the second turn (regression guard against a flaky race reappearing).
- No test depends on the real snapshot or real LLM output — those are exercised only in actual benchmark runs, not in CI-style `pytest tests/`.

## 8. Privacy / git hygiene

`chroma_data_benchmark/` and `benchmark/data/real_recall_cases.json` both derive from real private conversation content and must never be committed. `chroma_data/` is already `.gitignore`d (`.gitignore:83`); add `chroma_data_benchmark/` and `benchmark/data/` alongside it as part of implementation.

## 9. Open items deferred (YAGNI for this pass)

- Automatic snapshot refresh scheduling (manual re-run of the snapshot script is sufficient for now).
- Cross-run trend tracking / dashboards beyond the existing `--output`/`--csv` JSON export already in `benchmark/__main__.py`.
- Rewriting the 16 existing synthetic datasets to the multi-turn schema — they stay as-is; this is additive.
