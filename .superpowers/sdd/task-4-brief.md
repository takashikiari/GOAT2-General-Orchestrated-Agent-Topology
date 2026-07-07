### Task 4: Orchestrator pre-generates doc_id, stores l3_id in L2 messages

**Files:**
- Modify: `orchestrator/orchestrator.py` — `run()` method, around lines 420-433 (archive_task creation) and lines 422-426 (L2 message save)

**Interfaces:**
- Consumes: `_archive_turn(layers, chat_id, intent, reply, topic_id, doc_id)` from Task 3
- Consumes: `layers.append_and_save_working_context(chat_id, user_msg, assistant_msg)` — existing

- [ ] **Step 1: Locate exact run() lines**

Read `orchestrator/orchestrator.py` lines 237-260 to find where `run()` starts, then 419-433 for the save section.

- [ ] **Step 2: Modify orchestrator.py — pre-generate doc_id before L2 save**

In the `run()` method, replace lines 419-433 (the save section) with:

```python
            collector.start_stage("save")
            saved_reply = f"[Tool calls]\n{tool_summary}\n\n{reply}" if tool_summary else reply
            now = time.time()
            l3_doc_id = str(uuid.uuid4())
            await layers.append_and_save_working_context(
                chat_id,
                {"role": "user", "content": intent, "timestamp": now, "l3_id": l3_doc_id},
                {"role": "assistant", "content": saved_reply, "timestamp": now, "l3_id": l3_doc_id},
            )
            archive_task = asyncio.create_task(
                _archive_turn(
                    layers, chat_id, intent, saved_reply,
                    topic_id=current_activation.topic_id if current_activation else "",
                    doc_id=l3_doc_id,
                ))
            self._pending_archives.add(archive_task)
            archive_task.add_done_callback(self._pending_archives.discard)
            collector.end_stage("save")
```

(`uuid` is already imported at the top of orchestrator.py.)

- [ ] **Step 3: Verify import of uuid exists**

```bash
grep -n "^import uuid" /home/lenovo/workspace/goat2/orchestrator/orchestrator.py
```
Expected: line 7 or similar — `import uuid`. If absent, add it to the imports block.

- [ ] **Step 4: Run existing tests to verify no regressions**

```bash
cd /home/lenovo/workspace/goat2 && python -m pytest tests/ -v -x 2>&1 | tail -20
```
Expected: All previously passing tests still PASS.

- [ ] **Step 5: Commit**

```bash
git add orchestrator/orchestrator.py
git commit -m "feat: pre-generate l3_doc_id per turn, store as l3_id in L2 messages"
```

---

