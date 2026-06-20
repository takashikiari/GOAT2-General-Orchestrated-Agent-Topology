### Task 1: Add tunables to `config/limits.py`

**Files:**
- Modify: `config/limits.py:48-49` (in the Faza 2 block)

- [ ] **Step 1: Add the three constants**

In `config/limits.py`, just after `DEFAULT_EPISODIC_TOP_K: Final[int] = 5`, insert:

```python
# Faza 2 Commit 2: episodic recall cache tunables.
EPISODIC_CACHE_MAX_SIZE: Final[int] = 256
"""LRU cap for the episodic recall cache (entries)."""

EPISODIC_CACHE_TTL_S: Final[float] = 60.0
"""Per-entry TTL for cached episodic recall results (seconds)."""

EPISODIC_CACHE_TURN_BUCKET: Final[int] = 5
"""Refresh the cache bucket every N turns — bounds staleness without a per-query clock."""
```

Also extend `__all__` (the list at the top of the file) by adding the three names inside the `"# Faza 2"` comment block — add them right after `"DEFAULT_EPISODIC_TOP_K"`. They are part of the same Faza 2 family.

- [ ] **Step 2: Verify the file still parses**

Run: `python -c "from config.limits import EPISODIC_CACHE_MAX_SIZE, EPISODIC_CACHE_TTL_S, EPISODIC_CACHE_TURN_BUCKET; print(EPISODIC_CACHE_MAX_SIZE, EPISODIC_CACHE_TTL_S, EPISODIC_CACHE_TURN_BUCKET)"`
Expected: `256 60.0 5`

- [ ] **Step 3: Commit**

```bash
git add config/limits.py
git commit -m "feat(config): Faza 2 Commit 2 — episodic recall cache tunables"
```

---

