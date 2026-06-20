### Task 6: Invalidate cache from `store_and_promote`

**Files:**
- Modify: `supervisor/session/turn_persistence.py:191-241`

The cache must be invalidated after a turn is persisted, because the new turn is now part of working memory and could be promoted to episodic in the background — meaning the next recall should NOT serve stale results.

- [ ] **Step 1: Add invalidation as the last step of `store_and_promote`**

In `supervisor/session/turn_persistence.py`, inside `store_and_promote`, add at the end (just before the `except Exception` block):

```python
        # 5. Invalidate the episodic recall cache. We persist BEFORE
        #    invalidating so the cache observes the freshest state
        #    on its next call. Best-effort: cache failures must
        #    never break turn persistence.
        try:
            from supervisor.session.episodic_cache import get_episodic_cache
            get_episodic_cache().invalidate()
        except Exception as exc:  # noqa: BLE001 — best-effort
            log.debug("episodic cache invalidate failed: %s", exc)
```

Also extend the function's docstring with one line about cache invalidation:

```python
    """Persist the turn, learn style, refresh cache, schedule promotion.

    Args:
        supervisor: The live GoatSupervisor (source of mm, registry).
        turn_count: 1-based turn number (``len(history.messages)``).
        intent: The raw user intent for this turn.
        summary: The assistant's user-facing summary for this turn.

    Returns:
        None. Best-effort; never raises.

    Side effects:
        - Writes two records to working memory (``turn:N:intent``,
          ``turn:N:summary``).
        - Writes a JSON action log to ``turn:N:actions`` (if any
          tools were called).
        - Refreshes the in-memory style cache if a style update
          was learned.
        - Invalidates the process-local episodic recall cache so
          the next recall observes the freshest memory state.
        - Schedules a background task to promote old working
          entries to episodic.
    """
```

- [ ] **Step 2: Add an integration test for the invalidation hook**

Append to `tests/test_episodic_cache.py`:

```python
# ── Integration: store_and_promote invalidates the cache ─────────────────


def test_store_and_promote_invalidates_episodic_cache(monkeypatch):
    """After ``store_and_promote`` runs, the cache must be empty so
    the next recall observes the freshly-persisted turn."""
    from supervisor.session import turn_persistence
    from supervisor.session.episodic_cache import get_episodic_cache

    # Pre-populate the cache with a stale value.
    cache = get_episodic_cache()
    cache.put(("hello", "goat", 5, 0), [_episodic_hit("stale")])
    assert cache.size == 1

    # Build a minimal supervisor stub.
    supervisor = MagicMock()
    supervisor.memory_manager = MagicMock()
    supervisor._history = None
    supervisor._last_turn_result = None

    # schedule_promotion is fire-and-forget; stub it so the test
    # doesn't need a full ServiceRegistry.
    monkeypatch.setattr(
        turn_persistence, "schedule_promotion",
        lambda *a, **kw: None,
    )

    asyncio_run(turn_persistence.store_and_promote(supervisor, 1, "intent", "summary"))
    assert cache.size == 0
```

- [ ] **Step 3: Run the cache tests**

Run: `pytest tests/test_episodic_cache.py -v 2>&1 | tail -25`
Expected: ALL 17 tests pass (16 from Task 3 + 1 new invalidation test).

- [ ] **Step 4: Run the full memory-related test suite for regression**

Run: `pytest tests/test_episodic_cache.py tests/test_three_layer_memory.py tests/test_action_log.py tests/test_system_prompt_self_report.py -v 2>&1 | tail -10`
Expected: ALL pass.

- [ ] **Step 5: Commit**

```bash
git add supervisor/session/turn_persistence.py tests/test_episodic_cache.py
git commit -m "feat(turn_persistence): invalidate episodic cache after turn persist"
```

---

