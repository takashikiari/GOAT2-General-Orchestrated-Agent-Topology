### Task 4: Wire `fetch_episodic_hits` to consult the cache

**Files:**
- Modify: `supervisor/session/memory_helpers.py:160-186`

- [ ] **Step 1: Update the signature to take `turn_number` and consult the cache**

In `supervisor/session/memory_helpers.py`, replace the `fetch_episodic_hits` function (lines 160-186) with:

```python
async def fetch_episodic_hits(
    mm: "MemoryManager",
    query: str,
    top_k: int = _EPISODIC_DEFAULT_TOP_K,
    *,
    timeout_s: float = _EPISODIC_TIMEOUT_S,
    turn_number: int = 0,
) -> list:
    """Fetch episodic recall hits with a hard timeout and LRU+TTL cache.

    Cache key = ``build_episodic_cache_key(query, SESSION_ROLE, top_k,
    turn_number)``. On a hit, returns the cached value without
    issuing a recall call. On miss (or cache error), issues the
    recall and stores the result.

    The cache is a process-local singleton; tests inject a fresh
    cache via ``set_episodic_cache``.

    On any failure (timeout, exception, missing method), returns
    ``[]`` — the [Present-Past] layer renders without episodic
    hits but the rest of the structure is preserved.
    """
    # 1. Cache lookup (best-effort).
    from config.roles import SESSION_ROLE  # local: avoid circular at import
    from supervisor.session.episodic_cache import (
        build_episodic_cache_key,
        get_episodic_cache,
    )
    cache = get_episodic_cache()
    key = build_episodic_cache_key(query, SESSION_ROLE, top_k, turn_number)
    cached = cache.get(key)
    if cached is not None:
        return list(cached)

    # 2. Cache miss → real recall with timeout.
    try:
        hits = await asyncio.wait_for(
            mm.recall(SESSION_ROLE, query, limit=top_k),
            timeout=timeout_s,
        )
        result = list(hits or [])
    except asyncio.TimeoutError:
        log.warning(
            "fetch_episodic_hits: timed out after %.1fs", timeout_s,
        )
        return []
    except Exception as exc:  # noqa: BLE001
        log.debug("fetch_episodic_hits failed: %s", exc)
        return []

    # 3. Store in cache (best-effort). Note: an empty list IS a
    #    valid cache value (the query genuinely returned nothing)
    #    — caching it avoids hammering ChromaDB on the same empty
    #    query inside the TTL window.
    cache.put(key, result)
    return result
```

- [ ] **Step 2: Run the cache tests**

Run: `pytest tests/test_episodic_cache.py -v 2>&1 | tail -25`
Expected: ALL 16 tests pass.

- [ ] **Step 3: Run the three-layer memory tests to confirm no regression**

Run: `pytest tests/test_three_layer_memory.py -v 2>&1 | tail -30`
Expected: ALL pass. The existing `mem_turn` tests still work because `turn_number` is optional (`int = 0`).

- [ ] **Step 4: Commit**

```bash
git add supervisor/session/memory_helpers.py
git commit -m "feat(memory_helpers): wire episodic recall cache into fetch_episodic_hits"
```

---

