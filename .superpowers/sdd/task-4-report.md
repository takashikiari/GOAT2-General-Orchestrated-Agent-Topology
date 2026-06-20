# Task 4 Report: Wire `fetch_episodic_hits` to consult the cache

**Status:** DONE_WITH_CONCERNS

## What I implemented

Replaced `fetch_episodic_hits` in `supervisor/session/memory_helpers.py` with the brief's version, with two adjustments per instructions:
- Used module-level `SESSION_ROLE` (line 34) instead of the brief's local import
- Used module-level imports for `build_episodic_cache_key` and `get_episodic_cache` from `supervisor.session.episodic_cache`

The new signature is:
```python
async def fetch_episodic_hits(
    mm: "MemoryManager",
    query: str,
    top_k: int = _EPISODIC_DEFAULT_TOP_K,
    *,
    timeout_s: float = _EPISODIC_TIMEOUT_S,
    turn_number: int = 0,
) -> list
```

Cache lookup happens before the recall call (best-effort, swallows errors → falls through to recall). The recall is wrapped in the same `asyncio.wait_for` timeout as before. Results are cached (including `[]` to avoid hammering ChromaDB on empty queries).

## Files changed

- `supervisor/session/memory_helpers.py`: replaced `fetch_episodic_hits` (lines 160-186) with the cached version; added module-level imports for `build_episodic_cache_key` and `get_episodic_cache` (lines 35-39). File grew from 200 → 227 lines (well under the 260 ceiling).
- `tests/test_three_layer_memory.py`: added an autouse `_fresh_episodic_cache` fixture that injects a fresh `EpisodicRecallCache` before each test and clears it after. Required because the cache singleton is process-local and the three-layer tests share the same `(intent="intent", SESSION_ROLE, top_k=5, turn_number=0)` key — without isolation, the first test that calls `fetch_episodic_hits` with the default (empty) mock would cache `[]`, and later tests supplying real episodic hits would get the stale cached `[]` back.

## Cache test results (16/16 pass)

```
tests/test_episodic_cache.py::test_build_key_normalizes_intent_case_and_whitespace PASSED
tests/test_episodic_cache.py::test_build_key_includes_role_limit_and_turn_bucket PASSED
tests/test_episodic_cache.py::test_build_key_clamps_negative_turn_number_to_bucket_zero PASSED
tests/test_episodic_cache.py::test_build_key_empty_intent_yields_empty_string PASSED
tests/test_episodic_cache.py::test_cache_miss_returns_none_and_increments_counter PASSED
tests/test_episodic_cache.py::test_cache_put_then_get_returns_value_and_bumps_hit PASSED
tests/test_episodic_cache.py::test_cache_ttl_expiry_evicts_entry PASSED
tests/test_episodic_cache.py::test_cache_lru_evicts_oldest_when_full PASSED
tests/test_episodic_cache.py::test_cache_lru_bump_on_hit_protects_entry_from_eviction PASSED
tests/test_episodic_cache.py::test_cache_invalidate_drops_everything PASSED
tests/test_episodic_cache.py::test_cache_constructor_rejects_zero_or_negative_size PASSED
tests/test_episodic_cache.py::test_singleton_setter_swaps_instance PASSED
tests/test_episodic_cache.py::test_fetch_episodic_hits_uses_cache_on_second_call PASSED
tests/test_episodic_cache.py::test_fetch_episodic_hits_bypasses_cache_on_different_intent PASSED
tests/test_episodic_cache.py::test_fetch_episodic_hits_bypasses_cache_on_different_limit PASSED
tests/test_episodic_cache.py::test_fetch_episodic_hits_bypasses_cache_on_different_turn_bucket PASSED
============================== 16 passed in 0.99s ==============================
```

## Three-layer test results (15/15 pass)

```
tests/test_three_layer_memory.py::test_config_defaults_define_three_layers PASSED
tests/test_three_layer_memory.py::test_layer_label_constants_exported PASSED
tests/test_three_layer_memory.py::test_present_layer_only_contains_fresh_entries PASSED
tests/test_three_layer_memory.py::test_recent_entries_routed_to_present_past PASSED
tests/test_three_layer_memory.py::test_present_past_includes_episodic_recall PASSED
tests/test_three_layer_memory.py::test_present_past_caps_episodic_top_k PASSED
tests/test_three_layer_memory.py::test_old_entries_routed_to_past PASSED
tests/test_three_layer_memory.py::test_past_includes_letta_persona PASSED
tests/test_three_layer_memory.py::test_past_layer_unavailable_when_letta_down PASSED
tests/test_three_layer_memory.py::test_present_layer_caps_at_max_entries PASSED
tests/test_three_layer_memory.py::test_present_past_layer_caps_at_max_entries PASSED
tests/test_three_layer_memory.py::test_output_has_three_section_headers PASSED
tests/test_three_layer_memory.py::test_empty_layers_still_render_headers PASSED
tests/test_three_layer_memory.py::test_mm_none_returns_unavailable_marker PASSED
tests/test_three_layer_memory.py::test_recall_failure_doesnt_break_present_past PASSED
============================== 15 passed in 0.95s ==============================
```

Both files pass together: 31/31.

## Self-review findings

- All 16 cache tests pass: YES
- All three-layer memory tests still pass: YES (15/15)
- Module-level imports added correctly (not local imports): YES — `build_episodic_cache_key` and `get_episodic_cache` are imported at module level alongside `SESSION_ROLE`
- `turn_number: int = 0` default preserves backward compatibility: YES — the existing call site in `mem_inject.py:206` (`fetch_episodic_hits(mm, intent, _episodic_top_k)`) works unchanged
- Module line count under 260: YES (227 lines)
- Commit message exactly as specified: YES

## Concerns

1. **Scope creep on the test file.** The brief scoped Task 4 to only `supervisor/session/memory_helpers.py`, but adding the cache exposed a test-isolation bug in `tests/test_three_layer_memory.py`. Without the fixture, the cache singleton's state leaked between tests and caused 2 of 15 three-layer tests to fail. I added the autouse fixture (10 lines, mirroring the pattern in `tests/test_episodic_cache.py`). This is a legitimate test-isolation fix, not a workaround — once Task 5 lands (threading real `turn_number` through `mem_turn`), every turn will compute a different cache key and the leak goes away naturally. I committed both files together as one coherent "wire the cache" change rather than split them and leave an intermediate broken state.

2. **Pre-existing failures unrelated to this change.** `tests/tools/test_file_executor.py` and a few other files (`test_critic_fallback.py`, `test_memory_pipeline.py`, `tests/memory/test_temporal_memory.py`) fail with `ModuleNotFoundError` for `supervisor.workflow` and `tools.file_executor_helpers`. I verified by stashing my changes and re-running — the failures are present on the pre-existing `main` commit and have nothing to do with the cache wiring.

## Commits created

- `c76272a` — feat(memory_helpers): wire episodic recall cache into fetch_episodic_hits