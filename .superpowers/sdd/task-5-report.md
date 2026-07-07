### Task 5 Report: auto_promote enrichment at L2 trim time

**Status:** DONE
**Commit:** f1dcbbe

---

**What was done:**

1. **Tests written first** (`tests/test_auto_promote_enrichment.py`) — 3 tests covering:
   - No enrichment when no surplus (under-cap)
   - Enrichment fires for dropped user+assistant pair with `l3_id`
   - Old-format messages without `l3_id` are dropped but not enriched

2. **`memory/enrichment.py`**: Added `pair_and_enrich_dropped(dropped, episodic, extractor)` — groups dropped messages by `l3_id`, skips messages without it, calls `enrich_l3_entry` per pair. File went from 54 to 82 lines (within 90-line limit).

3. **`memory/auto_promote.py`**: Updated `maybe_auto_promote` and `schedule_auto_promote` to accept optional `episodic=None, extractor=None`. Inside `maybe_auto_promote`, accumulates `all_dropped` during the while loop (under the lock), then after the lock releases fires `asyncio.create_task(pair_and_enrich_dropped(...))` as fire-and-forget. Used local import for `pair_and_enrich_dropped` to avoid circular dependency. File went from 85 to 86 lines (within 90-line limit).

4. **`memory/layers.py`**: `MemoryLayers.__init__` gained `extractor=None` param, stored as `self._extractor`. The `schedule_auto_promote` call in `append_and_save_working_context` now passes `episodic=self._episodic, extractor=self._extractor`.

---

**Test results:**
- `tests/test_auto_promote_enrichment.py` + `tests/test_enrichment.py`: 9/9 passed
- Full suite: 176/176 passed

---

**Line counts:**
- `memory/auto_promote.py`: 86 lines (90-line limit OK)
- `memory/enrichment.py`: 82 lines (90-line limit OK)

---

**Concerns / caveats:**

- Existing `tests/test_auto_promote.py` calls `maybe_auto_promote("c", w)` positionally without `episodic`/`extractor` kwargs; backward compatibility is maintained, those tests continue to pass.
- `asyncio.create_task` requires a running event loop at call time. This is always satisfied inside an async context (after `async with working.chat_lock`). In tests, `asyncio.run(maybe_auto_promote(...))` provides the loop, so `create_task` fires and completes within that event loop before `run` exits.
