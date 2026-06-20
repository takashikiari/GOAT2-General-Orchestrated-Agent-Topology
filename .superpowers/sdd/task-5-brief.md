### Task 5: Thread `turn_number` through `mem_turn` and `run_turn`

**Files:**
- Modify: `supervisor/session/mem_inject.py:154-224`
- Modify: `supervisor/turn_runner.py:40-100`

This is the only wiring change that reaches the production call path. `turn_number` is the number of completed turns so far (i.e. `len(history.messages)` BEFORE adding the current user turn as pending). It's available right where `mem_turn` is invoked.

- [ ] **Step 1: Add `turn_number` parameter to `mem_turn` and forward to `fetch_episodic_hits`**

In `supervisor/session/mem_inject.py`, modify the `mem_turn` signature and the `fetch_episodic_hits` call site:

Change the signature (line 154-157):
```python
async def mem_turn(
    mm: "MemoryManager | None",
    intent: str,
    *,
    turn_number: int = 0,
) -> str:
```

Change the call site (line 206):
```python
    # 4. Fetch episodic recall hits (timeout-protected + cached).
    episodic_hits = await fetch_episodic_hits(
        mm, intent, _episodic_top_k, turn_number=turn_number,
    )
    episodic_hits = episodic_hits[:_episodic_top_k]
```

Also extend `__all__` if `mem_turn`'s new kwarg is exported (it isn't — the function is the public API, the kwarg is just a parameter, so `__all__` is fine as-is).

- [ ] **Step 2: Pass `turn_number` from `run_turn`**

In `supervisor/turn_runner.py`, replace the `mem_turn` call (around line 84):

Current:
```python
        mem_ctx = await mem_turn(supervisor.memory_manager, intent)
```

New:
```python
        # turn_number = completed turns before this one (i.e. history
        # length BEFORE buffering the current user turn as pending).
        # Used as the episodic recall cache bucket key.
        turn_number = (
            len(supervisor._history.messages)
            if supervisor._history is not None else 0
        )
        mem_ctx = await mem_turn(
            supervisor.memory_manager, intent, turn_number=turn_number,
        )
```

- [ ] **Step 3: Run the cache tests + three-layer memory tests**

Run: `pytest tests/test_episodic_cache.py tests/test_three_layer_memory.py -v 2>&1 | tail -40`
Expected: ALL pass.

- [ ] **Step 4: Run the action-log + supervisor tests for regression**

Run: `pytest tests/test_action_log.py tests/test_system_prompt_self_report.py -v 2>&1 | tail -30`
Expected: ALL pass.

- [ ] **Step 5: Commit**

```bash
git add supervisor/session/mem_inject.py supervisor/turn_runner.py
git commit -m "feat(mem_inject,turn_runner): thread turn_number for episodic cache key"
```

---

