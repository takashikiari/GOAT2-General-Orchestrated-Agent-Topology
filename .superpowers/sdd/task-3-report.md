# Task 3 Report: doc_id chain — store() returns doc_id, store_episodic() returns doc_id, _archive_turn() accepts doc_id

**Status:** DONE

## Commit

`1a5276e` — feat: store() returns doc_id, store_episodic/archive_turn accept pre-generated doc_id

## Test Output

```
3 passed in tests/test_doc_id_chain.py
167 passed, 0 failed — full suite (--ignore=tests/test_gliner_extractor.py)
```

## What Was Done

### memory/episodic/episodic.py
- Added `doc_id: str | None = None` param to `store()`
- Changed `doc_id = str(uuid.uuid4())` → `doc_id = doc_id or str(uuid.uuid4())`
- Added `return doc_id` (return type None → str)

### memory/layers.py
- Added `doc_id: str | None = None` param to `store_episodic()`
- Changed return type None → str
- Changed `await self._episodic.store(...)` → `return await self._episodic.store(..., doc_id=doc_id)`

### orchestrator/orchestrator.py
- Added `doc_id: str | None = None` param to `_archive_turn()`
- Passed `doc_id=doc_id` through to `layers.store_episodic()`
- Updated debug log to include `doc_id`

### tests/test_doc_id_chain.py (new, 3 tests)
- `test_store_returns_string` — verifies return is a 36-char UUID string
- `test_store_uses_provided_doc_id` — verifies pre-generated doc_id is used and passed to col.add
- `test_store_episodic_returns_string` — verifies layers.store_episodic returns the episodic.store result

### Incidental fixes (required for zero regressions)
`_FakeLayers` (tests/_orch_fakes.py) and `_TopicCaptureLayers` (tests/test_orchestrator_memory_flow.py) both had `store_episodic` without the `doc_id` kwarg. Updated both to match the new signature and return a str doc_id.

## Concerns

None. The `_write_lock` behavior is unchanged — `doc_id` is captured in the closure before lock acquisition. The return-type change (None → str) is fully backward-compatible.
