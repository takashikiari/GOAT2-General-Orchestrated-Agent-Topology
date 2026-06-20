# Task 3 Report — Faza 2 Commit 2: episodic recall cache failing test suite

## What I implemented

Created `tests/test_episodic_cache.py` verbatim from the brief in
`/home/lenovo/workspace/goat2/.superpowers/sdd/task-3-brief.md`. The file
contains:

- 12 unit tests for `EpisodicRecallCache` (key builder, LRU, TTL,
  invalidation, constructor validation, singleton setter).
- 4 integration tests that exercise `fetch_episodic_hits` with a
  `turn_number` keyword and assert the cache is consulted.

No other file was touched.

## Verification command output

Command: `pytest tests/test_episodic_cache.py -v 2>&1 | tail -40`

Verbatim tail:

```
        def test_fetch_episodic_hits_bypasses_cache_on_different_intent():
            """A different intent must produce a fresh recall call (no false hit)."""
            from supervisor.session.memory_helpers import fetch_episodic_hits

            mm = _mm_with_recall([_episodic_hit("x")])
    >       asyncio_run(fetch_episodic_hits(mm, "hello", 5, turn_number=10))
                        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    E       TypeError: fetch_episodic_hits() got an unexpected keyword argument 'turn_number'

    tests/test_episodic_cache.py:191: TypeError
    __________ test_fetch_episodic_hits_bypasses_cache_on_different_limit __________

            def test_fetch_episodic_hits_bypasses_cache_on_different_limit():
                """Different `limit` → different key → fresh recall."""
                from supervisor.session.memory_helpers import fetch_episodic_hits

                mm = _mm_with_recall([_episodic_hit("x")])
        >       asyncio_run(fetch_episodic_hits(mm, "hello", 5, turn_number=10))
                            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
        E       TypeError: fetch_episodic_hits() got an unexpected keyword argument 'turn_number'

        tests/test_episodic_cache.py:201: TypeError
        _______ test_fetch_episodic_hits_bypasses_cache_on_different_turn_bucket _______

                def test_fetch_episodic_hits_bypasses_cache_on_different_turn_bucket():
                    """turn_number // 5 shifts the bucket — different bucket → fresh recall."""
                    from supervisor.session.memory_helpers import fetch_episodic_hits

                    mm = _mm_with_recall([_episodic_hit("x")])
            >       asyncio_run(fetch_episodic_hits(mm, "hello", 5, turn_number=10))  # bucket 2
                                ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
            E       TypeError: fetch_episodic_hits() got an unexpected keyword argument 'turn_number'

            tests/test_episodic_cache.py:211: TypeError
            =========================== short test summary info ============================
            FAILED tests/test_episodic_cache.py::test_fetch_episodic_hits_uses_cache_on_second_call
            FAILED tests/test_episodic_cache.py::test_fetch_episodic_hits_bypasses_cache_on_different_intent
            FAILED tests/test_episodic_cache.py::test_fetch_episodic_hits_bypasses_cache_on_different_limit
            FAILED tests/test_episodic_cache.py::test_fetch_episodic_hits_bypasses_cache_on_different_turn_bucket
            ========================= 4 failed, 12 passed in 1.13s =========================
```

Result: **12 passed, 4 failed**, exactly as the brief expected. The 4
failures are all the same signature-mismatch signature expected for the
red phase:

```
TypeError: fetch_episodic_hits() got an unexpected keyword argument 'turn_number'
```

## Files changed

- Created: `/home/lenovo/workspace/goat2/tests/test_episodic_cache.py`
  (213 lines, copied verbatim from the brief).

## Git

Commit: `2c67967 test(episodic_cache): Faza 2 Commit 2 — cache + integration tests (4 fail)`

Only the new test file changed in the tracked tree. The `.pyc` and
`.superpowers/` were already dirty before this task and were not touched.

## Self-review findings

- [x] File copied verbatim from brief — content matches the code block
  lines 12–226 of `task-3-brief.md` (verified by re-reading both).
- [x] `pytest` ran cleanly: `4 failed, 12 passed in 1.13s`.
- [x] The 4 failing tests all surface the expected
  `TypeError: fetch_episodic_hits() got an unexpected keyword argument 'turn_number'`.
- [x] I did NOT modify the integration tests to make them pass.
- [x] Commit message is exactly the prescribed string, including the
  "(4 fail)" suffix.
- [x] No other file was modified or created.

## Concerns

None. The TDD red phase is in place; Task 4's job is to add the
`turn_number` parameter to `fetch_episodic_hits` and wire the cache, at
which point these 4 tests should turn green.
