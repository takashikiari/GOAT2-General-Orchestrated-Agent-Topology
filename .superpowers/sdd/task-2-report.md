### Task 2 Report: L3 Enrichment Helper + update_metadata

**Status:** DONE

**Commits:** `bb5cec9`

**Files changed:**
- `memory/enrichment.py` (54 lines, NEW) — `compute_importance(user_msg, assistant_msg) -> float` heuristic and `async enrich_l3_entry(...)` best-effort enrichment with entities, memory_type, importance.
- `memory/episodic/queries.py` (+19 lines) — added `async update_metadata(doc_id, updates)` method with write-lock safety.
- `tests/test_enrichment.py` (61 lines, NEW) — 6 unit tests covering importance scoring, enrichment with/without extractor, exception handling.

**Tests:**

```
6 passed in 0.06s
```

All tests green:
- `test_compute_importance_short` — word count < 20 yields score < 0.1 ✓
- `test_compute_importance_long` — 120 words yields 1.0 (capped) ✓
- `test_compute_importance_medium` — mid-range yields 0.0 < score < 1.0 ✓
- `test_enrich_l3_entry_calls_update_metadata` — enrichment updates include importance, entities, memory_type ✓
- `test_enrich_l3_entry_no_extractor` — fallback to "conversation" type when extractor=None ✓
- `test_enrich_l3_entry_handles_exception` — exceptions swallowed (no raise) ✓

**Constraints met:**
- ✓ enrichment.py ≤90 lines (54 lines)
- ✓ Single responsibility per file
- ✓ update_metadata holds _write_lock for all ChromaDB writes
- ✓ enrich_l3_entry swallows all exceptions (best-effort)
- ✓ TYPE_CHECKING guards for GLiNERExtractor and EpisodicMemory imports
- ✓ Code style: from __future__ annotations, get_logger(__name__), noqa BLE001
- ✓ queries.py at 165 lines accepted as mixin (grew organically per brief guidance)
