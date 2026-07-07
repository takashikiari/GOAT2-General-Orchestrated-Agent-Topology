### Task 5: auto_promote enrichment — enrich dropped L2 messages at trim time

**Files:**
- Modify: `memory/auto_promote.py` — add enrichment call for dropped messages with `l3_id`
- Modify: `memory/layers.py` — `append_and_save_working_context` passes episodic+extractor; `schedule_auto_promote` updated

**Interfaces:**
- Consumes: `enrich_l3_entry(doc_id, user_msg, assistant_msg, episodic, extractor)` from Task 2
- Consumes: `GLiNERExtractor` from Task 1 (optional — `None` if gliner not installed)
- Consumes: `EpisodicMemory` (from layers._episodic)
- Produces: `schedule_auto_promote(chat_id, working, episodic=None, extractor=None) -> None` (backward-compatible)
- Produces: `maybe_auto_promote(chat_id, working, episodic=None, extractor=None) -> None`

Notes:
- L2 messages now have `l3_id` field (set in Task 4) — but older messages won't. Only enrich if `l3_id` present.
- Pair messages: dropped messages come in as a flat list; user+assistant are adjacent pairs tagged by role.
- auto_promote.py is 85 lines — adding enrichment will push it over 90. Split: keep trim logic in auto_promote.py, move enrichment pairing to `memory/enrichment.py` as `pair_and_enrich_dropped(dropped, episodic, extractor)`.

- [ ] **Step 1: Write the failing tests**

```python
"""tests.test_auto_promote_enrichment — enrichment fires for dropped messages with l3_id."""
from __future__ import annotations
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


def _make_working(messages):
    working = MagicMock()
    working.chat_lock = MagicMock(return_value=MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock()))
    working.get_messages_raw = AsyncMock(return_value=messages)
    working.save_messages_raw = AsyncMock()
    return working


def test_no_enrichment_when_no_surplus():
    """When under cap, no trim and no enrichment."""
    from memory.auto_promote import maybe_auto_promote
    from memory.config import WORKING_MAX_MESSAGES
    messages = [{"role": "user", "content": "hi"} for _ in range(5)]
    working = _make_working(messages)
    episodic = MagicMock()
    episodic.update_metadata = AsyncMock()
    asyncio.run(maybe_auto_promote("chat1", working, episodic=episodic, extractor=None))
    episodic.update_metadata.assert_not_called()


def test_enrichment_fires_for_dropped_pair_with_l3_id():
    """Dropped user+assistant pair with l3_id triggers enrich_l3_entry."""
    from memory.auto_promote import maybe_auto_promote
    from memory.config import WORKING_MAX_MESSAGES
    # Build messages: 2 dropped + WORKING_MAX_MESSAGES kept
    dropped = [
        {"role": "user", "content": "hello", "l3_id": "doc-001"},
        {"role": "assistant", "content": "hi there", "l3_id": "doc-001"},
    ]
    kept = [{"role": "user", "content": f"msg{i}"} for i in range(WORKING_MAX_MESSAGES)]
    working = _make_working(dropped + kept)
    episodic = MagicMock()
    episodic.update_metadata = AsyncMock()
    asyncio.run(maybe_auto_promote("chat1", working, episodic=episodic, extractor=None))
    episodic.update_metadata.assert_called()
    call_args = episodic.update_metadata.call_args_list[0]
    assert call_args[0][0] == "doc-001"


def test_enrichment_skips_messages_without_l3_id():
    """Messages without l3_id (old format) are dropped but not enriched."""
    from memory.auto_promote import maybe_auto_promote
    from memory.config import WORKING_MAX_MESSAGES
    dropped = [
        {"role": "user", "content": "old message"},
        {"role": "assistant", "content": "old reply"},
    ]
    kept = [{"role": "user", "content": f"msg{i}"} for i in range(WORKING_MAX_MESSAGES)]
    working = _make_working(dropped + kept)
    episodic = MagicMock()
    episodic.update_metadata = AsyncMock()
    asyncio.run(maybe_auto_promote("chat1", working, episodic=episodic, extractor=None))
    episodic.update_metadata.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/lenovo/workspace/goat2 && python -m pytest tests/test_auto_promote_enrichment.py -v 2>&1 | head -20
```
Expected: FAIL (maybe_auto_promote signature mismatch).

- [ ] **Step 3: Add `pair_and_enrich_dropped` to `memory/enrichment.py`**

Append to the existing `memory/enrichment.py`:

```python
async def pair_and_enrich_dropped(
    dropped: list[dict],
    episodic: "EpisodicMemory",
    extractor: "GLiNERExtractor | None",
) -> None:
    """Extract user+assistant pairs from dropped L2 messages and enrich their L3 entries.

    Only messages with an ``l3_id`` field are enriched — older messages without
    this field (pre-Task 4 format) are silently skipped. Pairs are identified by
    matching ``l3_id`` across adjacent user/assistant messages.
    """
    by_l3_id: dict[str, dict] = {}
    for msg in dropped:
        l3_id = msg.get("l3_id")
        if not l3_id:
            continue
        role = msg.get("role", "")
        if l3_id not in by_l3_id:
            by_l3_id[l3_id] = {}
        by_l3_id[l3_id][role] = msg.get("content", "")

    for l3_id, roles in by_l3_id.items():
        user_msg = roles.get("user", "")
        assistant_msg = roles.get("assistant", "")
        if user_msg or assistant_msg:
            await enrich_l3_entry(l3_id, user_msg, assistant_msg, episodic, extractor)
```

Check that enrichment.py stays ≤90 lines after this addition:
```bash
wc -l /home/lenovo/workspace/goat2/memory/enrichment.py
```
If over 90 lines, the file needs splitting — move `compute_importance` and `enrich_l3_entry` to a submodule.

- [ ] **Step 4: Modify `memory/auto_promote.py` — update signature and call enrichment**

Replace the existing `maybe_auto_promote` and `schedule_auto_promote` functions:

```python
async def maybe_auto_promote(
    chat_id: str,
    working: WorkingMemory,
    episodic=None,
    extractor=None,
) -> None:
    """Trim L2 working memory to WORKING_MAX_MESSAGES and enrich dropped entries.

    For each dropped user+assistant pair that has an ``l3_id`` field, fires
    ``pair_and_enrich_dropped`` to update the corresponding L3 ChromaDB entry
    with GLiNER-extracted entities, memory_type, and importance.
    """
    from memory.enrichment import pair_and_enrich_dropped  # local import avoids circular
    async with working.chat_lock(chat_id):
        messages = await working.get_messages_raw(chat_id)
        total = len(messages)
        if total <= WORKING_MAX_MESSAGES:
            return
        surplus = total - WORKING_MAX_MESSAGES
        log.info(
            "auto_promote: chat=%s total=%d surplus=%d cap=%d",
            chat_id, total, surplus, WORKING_MAX_MESSAGES,
        )
        dropped_total = 0
        all_dropped: list[dict] = []
        while len(messages) > WORKING_MAX_MESSAGES:
            surplus_now = len(messages) - WORKING_MAX_MESSAGES
            chunk_size = min(PROMOTE_CHUNK_SIZE, surplus_now)
            dropped = messages[:chunk_size]
            messages = messages[chunk_size:]
            all_dropped.extend(dropped)
            dropped_total += len(dropped)
            await working.save_messages_raw(chat_id, messages)
            log.debug(
                "auto_promote: chat=%s chunk_dropped=%d total_dropped=%d remaining=%d",
                chat_id, len(dropped), dropped_total, len(messages),
            )
            await asyncio.sleep(0)

    log.info(
        "auto_promote: chat=%s done total_dropped=%d kept=%d",
        chat_id, dropped_total, WORKING_MAX_MESSAGES,
    )
    if all_dropped and episodic is not None:
        asyncio.create_task(pair_and_enrich_dropped(all_dropped, episodic, extractor))


def schedule_auto_promote(
    chat_id: str,
    working: WorkingMemory,
    episodic=None,
    extractor=None,
) -> None:
    """Fire-and-forget: schedule a background L2 trim + L3 enrichment for chat_id."""
    asyncio.create_task(maybe_auto_promote(chat_id, working, episodic=episodic, extractor=extractor))
```

Check line count:
```bash
wc -l /home/lenovo/workspace/goat2/memory/auto_promote.py
```
If over 90 lines, split: move the docstring header/imports to a smaller stub and put the implementation in a helper.

- [ ] **Step 5: Update `memory/layers.py` — pass episodic+extractor to schedule_auto_promote**

Find `schedule_auto_promote(chat_id, self._working)` in `append_and_save_working_context` and update:

```python
        schedule_auto_promote(chat_id, self._working, episodic=self._episodic, extractor=self._extractor)
```

Also update the `MemoryLayers.__init__` to accept and store extractor:

```python
    def __init__(
        self,
        working: "WorkingMemory",
        episodic: "EpisodicMemory",
        permanent: "PermanentMemory",
        cache_ttl: int = 300,
        extractor=None,
    ) -> None:
        self._working = working
        self._episodic = episodic
        self._permanent = permanent
        self._extractor = extractor
        self._cache = SessionCache(working, ttl_seconds=cache_ttl)
        # ... rest unchanged
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd /home/lenovo/workspace/goat2 && python -m pytest tests/test_auto_promote_enrichment.py tests/test_enrichment.py -v
```
Expected: All tests PASS.

- [ ] **Step 7: Commit**

```bash
git add memory/auto_promote.py memory/enrichment.py memory/layers.py tests/test_auto_promote_enrichment.py
git commit -m "feat: auto_promote enriches dropped L2 messages using GLiNER via l3_id link"
```

---

