### Task 4 Report: Orchestrator pre-generates doc_id, stores l3_id in L2 messages

**Status:** COMPLETE

**Commit:** `1e1c003`

**Test summary:** 173 passed in 2.35s — zero regressions

---

**What changed**

In `orchestrator/orchestrator.py`, the save section of `run()` (lines 425-442) was modified:

1. Added `l3_doc_id = str(uuid.uuid4())` immediately after computing `now`.
2. Both L2 messages (user and assistant) passed to `append_and_save_working_context` now carry `"l3_id": l3_doc_id`.
3. `_archive_turn(...)` now receives `doc_id=l3_doc_id` as a keyword argument (consumed by Task 3's updated signature).

No new imports were needed — `uuid` was already imported at line 8.

**Concerns:** None. The change is minimal (4 insertions / 2 deletions), all 173 existing tests pass, and the UUID is generated synchronously before any I/O so there is no race between the L2 write and the async L3 archive task.