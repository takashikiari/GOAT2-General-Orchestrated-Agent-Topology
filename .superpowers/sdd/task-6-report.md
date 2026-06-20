# Task 6 Report — Invalidate cache from `store_and_promote`

## What was implemented

Added the final production wiring that ties the episodic recall cache to the turn persistence flow:

1. **`supervisor/session/turn_persistence.py`**
   - Updated `store_and_promote` docstring to add a `Side effects` section listing all five effects, including the new line: "Invalidates the process-local episodic recall cache so the next recall observes the freshest memory state."
   - Added step 5 (cache invalidation) inside the existing `try` block, between `schedule_promotion(supervisor, turn_count)` and the `except Exception` block. The invalidation uses a nested `try/except Exception` with `log.debug` so cache failures never break turn persistence.
   - Imports `get_episodic_cache` lazily inside the function (consistent with the surrounding code's import style — `refresh_style` is also imported inline).

2. **`tests/test_episodic_cache.py`**
   - Appended `test_store_and_promote_invalidates_episodic_cache` integration test that pre-populates the cache with a stale value, runs `store_and_promote` against a minimal supervisor stub, and asserts `cache.size == 0`. Uses `monkeypatch.setattr` to stub `schedule_promotion` so the test doesn't need a full `ServiceRegistry`.

## Test results

```
$ python3 -m pytest tests/test_episodic_cache.py tests/test_three_layer_memory.py tests/test_action_log.py tests/test_system_prompt_self_report.py -v 2>&1 | tail -25
============================== 46 passed in 1.12s ==============================
```

Cache file alone:

```
$ python3 -m pytest tests/test_episodic_cache.py -v 2>&1 | tail -25
collected 17 items
...
tests/test_episodic_cache.py::test_store_and_promote_invalidates_episodic_cache PASSED [100%]
============================== 17 passed in 1.08s ==============================
```

All 17 cache tests pass (16 prior + 1 new invalidation test). The new test is the last one listed and passed.

## Files changed

- `/home/lenovo/workspace/goat2/supervisor/session/turn_persistence.py` (modified)
- `/home/lenovo/workspace/goat2/tests/test_episodic_cache.py` (appended)

## Self-review findings

- The cache invalidation is positioned AFTER `_store_turn`, `store_action_log`, `_learn_and_persist`, `refresh_style`, AND `schedule_promotion` — matching the brief which said the new step 5 goes between step 4 and the `except` block.
- Wrapped in `try/except Exception` with `log.debug` (best-effort) per the brief.
- New test passes.
- All 4 test files still pass (no regression). 46 tests total, all green.
- Did NOT touch any other file besides the two specified in the brief.
- Commit message is exactly as specified: `feat(turn_persistence): invalidate episodic cache after turn persist`.

## Concerns

None. The brief's note about the `_mm_with_recall` helper being unused for now is accurate — it remains unused in the test file but harmless (still available for future tests).

## Commit

```
e66243b feat(turn_persistence): invalidate episodic cache after turn persist
```