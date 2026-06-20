### Task 7: Final verification + changelog

**Files:**
- Modify: `CHANGELOG.md` (one-line entry)

- [ ] **Step 1: Run the full test suite**

Run: `pytest tests/ -q 2>&1 | tail -20`
Expected: zero failures. The full test suite is small (a few hundred tests at most) and finishes in seconds.

- [ ] **Step 2: Add a CHANGELOG entry**

In `CHANGELOG.md`, find the most recent entry and add a new bullet at the top:

```markdown
- Faza 2 Commit 2 — episodic recall cache: bounded LRU (256 entries,
  TTL 60s) in front of ChromaDB. Key = `(intent_normalized, role,
  limit, turn_number // 5)`. Invalidated on every `store_and_promote`.
  See `supervisor/session/episodic_cache.py` and
  `tests/test_episodic_cache.py`.
```

- [ ] **Step 3: Final commit**

```bash
git add CHANGELOG.md
git commit -m "docs: Faza 2 Commit 2 — episodic cache entry"
```

---

## Self-Review Checklist

- [x] Spec coverage: LRU 256, TTL 60s, invalidation on store, key = `(intent_normalized, role, limit, turn_number // 5)` — all four requirements covered by Tasks 1, 2, 4, 6.
- [x] Placeholder scan: no "TBD", "TODO", or vague "handle errors" — every step shows the actual code.
- [x] Type consistency: `EpisodicRecallCache.get`/`put`/`invalidate` consistent across Tasks 2, 3, 6. `fetch_episodic_hits` signature stable from Task 4 onwards. `mem_turn(turn_number=...)` kwarg matches in Tasks 1, 4, 5.
- [x] Backward compatibility: existing `fetch_episodic_hits` and `mem_turn` callers don't break because the new parameters have defaults (`turn_number: int = 0`).
- [x] Cache never raises: every public cache method wraps its body in `try/except` and logs at DEBUG. Verified by Tests 1, 2 in Task 3.
- [x] Module line ceiling: `episodic_cache.py` is ~150 lines, `memory_helpers.py` grows from 200 → 220 lines — both under the 260-line project cap.
