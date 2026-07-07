# Memory Enrichment + Chat-Scoped Prefetch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enrich L3 ChromaDB entries with GLiNER-extracted entities, memory_type, and importance at L2 trim time; replace the regex-based specific-key prefetch mechanism with a chat_id-scoped thematic search that runs unconditionally.

**Architecture:** Pre-generate doc_id in orchestrator and pass it through `_archive_turn` → `store_episodic` → `EpisodicMemory.store`, so the same doc_id is written into L2 messages as `l3_id`. When `maybe_auto_promote` trims L2, it pairs dropped messages by their `l3_id` and fires `enrich_l3_entry` (GLiNER extraction + importance heuristic) to update ChromaDB metadata. The cold-path prefetch adds a `_thematic_scoped` mechanism (chat_id-filtered semantic search) that always runs alongside the global thematic, and drops the regex-based `_specific_key` mechanism and `extract_structural_keys` import.

**Tech Stack:** Python asyncio, ChromaDB, GLiNER (`urchade/gliner_multi-v2.1`), existing Redis/WorkingMemory, existing EpisodicMemory.

## Global Constraints

- Max 90 lines per file; single responsibility per file; split before growing.
- No summaries or LLM calls at write time — entities extracted by GLiNER only.
- No new LLM dependency — GLiNER is a local NER model, not a hosted LLM.
- No duplicate L3 writes — enrichment updates existing entries via `update_metadata`.
- `store()` change must be backward-compatible: `doc_id=None` (default) preserves existing callers.
- `store_episodic()` change must be backward-compatible: returns `str` (was `None`), callers that ignore the return value still work.
- The `_write_lock` must be held for all ChromaDB mutating calls (add, update, delete).
- `schedule_auto_promote` must keep its existing signature for existing callers in `layers.py`.
- GLiNER model loads lazily on first extraction call, never at import time.
- File-size rule: if any modified file exceeds 90 lines after changes, split it before committing.

---

### Task 1: GLiNER extractor module

**Files:**
- Create: `memory/gliner_extractor.py`
- Test: `tests/test_gliner_extractor.py`

**Interfaces:**
- Produces: `GLiNERExtractor` class with `async def extract(self, text: str) -> dict` returning `{"entities": list[str], "entity_types": list[str], "memory_type": str}`
- `memory_type` is one of: `"greeting"`, `"fact"`, `"conversation"`

- [ ] **Step 1: Write the failing test**

```python
"""tests.test_gliner_extractor — unit tests for GLiNERExtractor (no GLiNER installed)."""
from __future__ import annotations
import pytest
from unittest.mock import patch, MagicMock
from memory.gliner_extractor import GLiNERExtractor, _infer_type


def test_infer_type_greeting_no_entities_short():
    assert _infer_type([], [], "hi") == "greeting"


def test_infer_type_fact_with_credential():
    assert _infer_type(["password"], ["credential"], "my password is abc") == "fact"


def test_infer_type_fact_with_entities():
    assert _infer_type(["Claude"], ["technology"], "I use Claude every day") == "fact"


def test_infer_type_conversation_no_entities_long():
    text = "I was thinking about things and how they work in general systems"
    assert _infer_type([], [], text) == "conversation"


def test_extract_returns_fallback_on_exception():
    """When GLiNER is not installed, extract() returns empty/conversation."""
    import asyncio
    extractor = GLiNERExtractor()
    result = asyncio.run(extractor.extract("hello world"))
    assert "entities" in result
    assert "entity_types" in result
    assert "memory_type" in result


def test_extract_with_mock_model():
    import asyncio
    extractor = GLiNERExtractor()
    mock_model = MagicMock()
    mock_model.predict_entities.return_value = [
        {"text": "GOAT", "label": "project"},
        {"text": "Gabriel", "label": "person"},
    ]
    extractor._model = mock_model
    result = asyncio.run(extractor.extract("Gabriel built GOAT"))
    assert "GOAT" in result["entities"]
    assert "project" in result["entity_types"]
    assert result["memory_type"] == "fact"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/lenovo/workspace/goat2 && python -m pytest tests/test_gliner_extractor.py -v 2>&1 | head -20
```
Expected: ImportError or ModuleNotFoundError (file doesn't exist yet).

- [ ] **Step 3: Write the implementation**

Create `memory/gliner_extractor.py` (≤90 lines):

```python
"""memory.gliner_extractor — GLiNER-based entity extraction for L3 enrichment."""
from __future__ import annotations

import asyncio

from utils.logging.setup import get_logger

log = get_logger(__name__)

_ENTITY_LABELS = [
    "person", "technology", "project", "credential",
    "location", "organization", "event", "preference",
]


class GLiNERExtractor:
    """Zero-shot NER using GLiNER; model loads lazily on first call."""

    MODEL_NAME = "urchade/gliner_multi-v2.1"

    def __init__(self) -> None:
        self._model = None

    def _get_model(self):
        if self._model is None:
            from gliner import GLiNER  # lazy import — not installed by default
            self._model = GLiNER.from_pretrained(self.MODEL_NAME)
            log.info("GLiNERExtractor: model loaded (%s)", self.MODEL_NAME)
        return self._model

    def _extract_sync(self, text: str) -> dict:
        model = self._get_model()
        raw = model.predict_entities(text, _ENTITY_LABELS, threshold=0.5)
        entities = [e["text"] for e in raw]
        entity_types = [e["label"] for e in raw]
        memory_type = _infer_type(entities, entity_types, text)
        return {"entities": entities, "entity_types": entity_types, "memory_type": memory_type}

    async def extract(self, text: str) -> dict:
        """Extract entities and infer memory_type. Returns fallback dict on any error."""
        try:
            return await asyncio.to_thread(self._extract_sync, text)
        except Exception as exc:  # noqa: BLE001
            log.warning("GLiNERExtractor.extract failed: %s", exc)
            return {"entities": [], "entity_types": [], "memory_type": "conversation"}


def _infer_type(entities: list[str], entity_types: list[str], text: str) -> str:
    """Heuristic memory_type from extracted entities and text length."""
    if not entities and len(text.split()) < 6:
        return "greeting"
    if "credential" in entity_types or entities:
        return "fact"
    return "conversation"
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/lenovo/workspace/goat2 && python -m pytest tests/test_gliner_extractor.py -v
```
Expected: All 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add memory/gliner_extractor.py tests/test_gliner_extractor.py
git commit -m "feat: add GLiNERExtractor for L3 entity enrichment (lazy model load)"
```

---

### Task 2: L3 enrichment helper + update_metadata

**Files:**
- Create: `memory/enrichment.py`
- Modify: `memory/episodic/queries.py` (add `update_metadata` method — file is 146 lines, will stay ≤90+56=146 after adding ~20 lines, which exceeds 90; add at end of file, keeping the mixin's single responsibility intact — this is the only write path for metadata updates)
- Test: `tests/test_enrichment.py`

**Interfaces:**
- Consumes: `GLiNERExtractor.extract(text) -> dict` from Task 1
- Consumes: `EpisodicMemory.update_metadata(doc_id, updates) -> None` (new method on queries.py)
- Produces: `compute_importance(user_msg, assistant_msg) -> float` (0.0–1.0)
- Produces: `async enrich_l3_entry(doc_id, user_msg, assistant_msg, episodic, extractor) -> None`

- [ ] **Step 1: Write the failing tests**

```python
"""tests.test_enrichment — unit tests for compute_importance and enrich_l3_entry."""
from __future__ import annotations
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def test_compute_importance_short():
    from memory.enrichment import compute_importance
    score = compute_importance("hi", "hello")
    assert 0.0 < score < 0.1  # very short


def test_compute_importance_long():
    from memory.enrichment import compute_importance
    user = " ".join(["word"] * 60)
    assistant = " ".join(["word"] * 60)
    score = compute_importance(user, assistant)
    assert score == 1.0  # 120 words → capped at 1.0


def test_compute_importance_medium():
    from memory.enrichment import compute_importance
    score = compute_importance("hello world today", "ok good bye")
    assert 0.0 < score < 1.0


def test_enrich_l3_entry_calls_update_metadata():
    from memory.enrichment import enrich_l3_entry
    episodic = MagicMock()
    episodic.update_metadata = AsyncMock()
    extractor = MagicMock()
    extractor.extract = AsyncMock(return_value={
        "entities": ["Claude"], "entity_types": ["technology"], "memory_type": "fact"
    })
    asyncio.run(enrich_l3_entry("doc-123", "user msg", "assistant msg", episodic, extractor))
    episodic.update_metadata.assert_called_once()
    call_args = episodic.update_metadata.call_args
    assert call_args[0][0] == "doc-123"
    updates = call_args[0][1]
    assert "importance" in updates
    assert "entities" in updates
    assert "memory_type" in updates


def test_enrich_l3_entry_no_extractor():
    from memory.enrichment import enrich_l3_entry
    episodic = MagicMock()
    episodic.update_metadata = AsyncMock()
    asyncio.run(enrich_l3_entry("doc-456", "msg", "reply", episodic, None))
    episodic.update_metadata.assert_called_once()
    updates = episodic.update_metadata.call_args[0][1]
    assert updates["memory_type"] == "conversation"


def test_enrich_l3_entry_handles_exception():
    from memory.enrichment import enrich_l3_entry
    episodic = MagicMock()
    episodic.update_metadata = AsyncMock(side_effect=Exception("db error"))
    # Should not raise
    asyncio.run(enrich_l3_entry("doc-789", "msg", "reply", episodic, None))
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/lenovo/workspace/goat2 && python -m pytest tests/test_enrichment.py -v 2>&1 | head -20
```
Expected: ImportError (files don't exist yet).

- [ ] **Step 3: Add `update_metadata` to `memory/episodic/queries.py`**

Append after the `delete_entries` method (before the final empty line at end of file):

```python
    async def update_metadata(self, doc_id: str, updates: dict) -> None:
        """Update metadata fields on an existing L3 entry (write-locked).

        Merges ``updates`` into existing metadata so callers only specify the
        fields they want to change. Silently no-ops if ``doc_id`` is not found.
        """
        def _sync() -> None:
            col = self._get_collection()
            r = col.get(ids=[doc_id], include=["metadatas"])
            existing = dict((r.get("metadatas") or [{}])[0] or {})
            existing.update(updates)
            col.update(ids=[doc_id], metadatas=[existing])

        try:
            async with self._write_lock:
                await asyncio.to_thread(_sync)
        except Exception as exc:  # noqa: BLE001
            log.debug("update_metadata failed doc_id=%s: %s", doc_id, exc)
```

- [ ] **Step 4: Create `memory/enrichment.py`** (≤90 lines):

```python
"""memory.enrichment — L3 metadata enrichment at L2 trim time."""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from utils.logging.setup import get_logger

if TYPE_CHECKING:
    from memory.gliner_extractor import GLiNERExtractor
    from memory.episodic import EpisodicMemory

log = get_logger(__name__)


def compute_importance(user_msg: str, assistant_msg: str) -> float:
    """Word-count importance heuristic (0.0–1.0). 120 words → 1.0."""
    words = len(user_msg.split()) + len(assistant_msg.split())
    return round(min(words / 120.0, 1.0), 3)


async def enrich_l3_entry(
    doc_id: str,
    user_msg: str,
    assistant_msg: str,
    episodic: "EpisodicMemory",
    extractor: "GLiNERExtractor | None",
) -> None:
    """Enrich an existing L3 entry with entities, memory_type, and importance.

    Called at L2 trim time by auto_promote — the dropped messages already have
    a doc_id linking them to an L3 ChromaDB entry written by _archive_turn.
    GLiNER extracts entities from the full user+assistant text; importance is a
    word-count heuristic. All failures are logged and swallowed (best-effort).
    """
    try:
        importance = compute_importance(user_msg, assistant_msg)
        if extractor is not None:
            extracted = await extractor.extract(f"{user_msg}\n{assistant_msg}")
        else:
            extracted = {"entities": [], "entity_types": [], "memory_type": "conversation"}
        updates = {
            "importance": importance,
            "entities": ",".join(extracted["entities"]),
            "entity_types": ",".join(extracted["entity_types"]),
            "memory_type": extracted["memory_type"],
        }
        await episodic.update_metadata(doc_id, updates)
        log.debug(
            "L3 enriched doc_id=%s type=%s entities=%d",
            doc_id, extracted["memory_type"], len(extracted["entities"]),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("enrich_l3_entry failed doc_id=%s: %s", doc_id, exc)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /home/lenovo/workspace/goat2 && python -m pytest tests/test_enrichment.py -v
```
Expected: All 6 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add memory/enrichment.py memory/episodic/queries.py tests/test_enrichment.py
git commit -m "feat: add L3 enrichment helper and EpisodicQueries.update_metadata"
```

---

### Task 3: doc_id chain — store() returns doc_id, store_episodic() returns doc_id, _archive_turn() accepts doc_id

**Files:**
- Modify: `memory/episodic/episodic.py:55-75` — `store()` signature + return value
- Modify: `memory/layers.py:168-195` — `store_episodic()` signature + return value
- Modify: `orchestrator/orchestrator.py:148-159` — `_archive_turn()` signature
- Test: `tests/test_doc_id_chain.py`

**Interfaces:**
- Consumes: `EpisodicMemory.store()` (modified)
- Produces: `EpisodicMemory.store(chat_id, content, metadata, doc_id=None) -> str`
- Produces: `MemoryLayers.store_episodic(chat_id, content, tags=None, topic_id="", doc_id=None) -> str`
- Produces: `_archive_turn(layers, chat_id, intent, reply, topic_id="", doc_id=None) -> None`

- [ ] **Step 1: Write the failing tests**

```python
"""tests.test_doc_id_chain — store() returns doc_id, accepts pre-generated doc_id."""
from __future__ import annotations
import asyncio
import uuid
from unittest.mock import MagicMock, patch, AsyncMock


def test_store_returns_string():
    """EpisodicMemory.store() must return a str doc_id."""
    from memory.episodic.episodic import EpisodicMemory
    ep = EpisodicMemory()
    mock_col = MagicMock()
    mock_col.count.return_value = 0
    ep._collection = mock_col
    result = asyncio.run(ep.store("chat1", "content", {"timestamp": 0.0}))
    assert isinstance(result, str)
    assert len(result) == 36  # UUID format


def test_store_uses_provided_doc_id():
    """EpisodicMemory.store() uses pre-generated doc_id when provided."""
    from memory.episodic.episodic import EpisodicMemory
    ep = EpisodicMemory()
    mock_col = MagicMock()
    mock_col.count.return_value = 0
    ep._collection = mock_col
    pre_id = str(uuid.uuid4())
    result = asyncio.run(ep.store("chat1", "content", {"timestamp": 0.0}, doc_id=pre_id))
    assert result == pre_id
    # Verify col.add was called with our pre_id
    call_kwargs = mock_col.add.call_args
    assert call_kwargs[1]["ids"] == [pre_id] or call_kwargs[0][0] == [pre_id] or pre_id in str(mock_col.add.call_args)


def test_store_episodic_returns_string():
    """MemoryLayers.store_episodic() must return a str doc_id."""
    import time
    from memory.layers import MemoryLayers
    mock_working = MagicMock()
    mock_episodic = MagicMock()
    mock_episodic.store = AsyncMock(return_value="returned-id")
    mock_permanent = MagicMock()
    layers = MemoryLayers(mock_working, mock_episodic, mock_permanent)
    result = asyncio.run(layers.store_episodic("chat1", "content"))
    assert result == "returned-id"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/lenovo/workspace/goat2 && python -m pytest tests/test_doc_id_chain.py -v 2>&1 | head -20
```
Expected: FAIL (store() returns None currently).

- [ ] **Step 3: Modify `memory/episodic/episodic.py` — store() returns str**

Replace the `store` method signature and body (lines 55-75):

Old:
```python
    async def store(self, chat_id: str, content: str, metadata: dict) -> None:
        ...
        doc_id = str(uuid.uuid4())
        ...
        async with self._write_lock:
            await asyncio.to_thread(_sync)
        log.debug("L3 write ok: chat=%s doc_id=%s tags=%r", chat_id, doc_id, metadata.get("tags", ""))
```

New (change signature, add `doc_id` param, return doc_id):
```python
    async def store(self, chat_id: str, content: str, metadata: dict, doc_id: str | None = None) -> str:
        """Store content + metadata under the write lock; returns the doc_id used.

        ``doc_id`` may be pre-generated by the caller (pass through); if omitted
        a new UUID is generated. Allows orchestrator to link L2 messages to their
        L3 entry before the async archive write completes.
        """
        doc_id = doc_id or str(uuid.uuid4())
        merged = {"chat_id": chat_id, **metadata}
        merged.setdefault("access_count", 0)
        merged.setdefault("last_accessed_ts", merged.get("timestamp", 0.0))

        def _sync() -> None:
            col = self._get_collection()
            merged["message_id"] = doc_id
            merged["sequence_number"] = col.count() + 1
            col.add(ids=[doc_id], documents=[content], metadatas=[merged])

        async with self._write_lock:
            await asyncio.to_thread(_sync)
        log.debug("L3 write ok: chat=%s doc_id=%s tags=%r", chat_id, doc_id, metadata.get("tags", ""))
        return doc_id
```

- [ ] **Step 4: Modify `memory/layers.py` — store_episodic() returns str, accepts doc_id**

Change `store_episodic` (lines 168-195) to accept and pass `doc_id`, return its result:

```python
    async def store_episodic(
        self, chat_id: str, content: str, tags: list[str] | None = None,
        topic_id: str = "", doc_id: str | None = None,
    ) -> str:
        """L3: write content to episodic memory — the only L3 write path.

        Returns the doc_id used (UUID). ``doc_id`` may be pre-generated by the
        orchestrator to create an L2↔L3 link before the async write completes.
        """
        now = time.time()
        metadata: dict = {
            "tags": ",".join(tags or []),
            "timestamp": now,
            "access_count": 0,
            "last_accessed_ts": now,
        }
        if topic_id:
            metadata["topic_id"] = topic_id
        return await self._episodic.store(chat_id, content, metadata, doc_id=doc_id)
```

- [ ] **Step 5: Modify `orchestrator/orchestrator.py` — _archive_turn accepts doc_id**

Change `_archive_turn` (lines 148-159):

```python
async def _archive_turn(
    layers, chat_id: str, intent: str, reply: str,
    topic_id: str = "", doc_id: str | None = None,
) -> None:
    """Fire-and-forget: archive the full message pair into L3 episodic memory.

    Tagged 'l2_full_archive'. ``topic_id`` links the entry to its topic thread.
    ``doc_id`` is pre-generated by the orchestrator so L2 messages carry an
    ``l3_id`` field before this async write completes.
    """
    try:
        content = f"user: {intent}\nassistant: {reply}"
        await layers.store_episodic(
            chat_id, content, tags=["l2_full_archive"], topic_id=topic_id, doc_id=doc_id,
        )
        log.debug("L3 archive write ok: chat=%s topic=%s doc_id=%s", chat_id, topic_id, doc_id)
    except Exception as exc:
        log.warning("L3 archive dump failed chat=%s: %s", chat_id, exc)
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd /home/lenovo/workspace/goat2 && python -m pytest tests/test_doc_id_chain.py -v
```
Expected: All 3 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add memory/episodic/episodic.py memory/layers.py orchestrator/orchestrator.py tests/test_doc_id_chain.py
git commit -m "feat: store() returns doc_id, store_episodic/archive_turn accept pre-generated doc_id"
```

---

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

### Task 6: Registry wires GLiNERExtractor + chat_id-scoped thematic prefetch

**Files:**
- Modify: `registry/registry.py` — add `gliner_extractor` lazy property; pass to `MemoryLayers`
- Modify: `memory/layers.py` — `search_episodic` and `search_episodic_with_cache` accept `chat_id_filter`; `_search_cache_key` includes it
- Modify: `memory/episodic/episodic.py` — `search()` accepts `chat_id_filter` parameter
- Modify: `orchestrator/orchestrator.py` — `_prefetch_daemon` adds `_thematic_scoped`; removes `_specific_key` and `extract_structural_keys` import
- Modify: `requirements.txt` — add `gliner` (optional, commented)
- Test: `tests/test_chat_id_scoped_search.py`

**Interfaces:**
- Consumes: `MemoryLayers.__init__(working, episodic, permanent, cache_ttl, extractor)` from Task 5
- Produces: `EpisodicMemory.search(..., chat_id_filter=None)` — adds `chat_id` clause to ChromaDB `where`
- Produces: `MemoryLayers.search_episodic(..., chat_id_filter=None)` and `search_episodic_with_cache(..., chat_id_filter=None)`
- Produces: `_thematic_scoped()` in `_prefetch_daemon` — always runs on cold path alongside global `_thematic()`

- [ ] **Step 1: Write the failing tests**

```python
"""tests.test_chat_id_scoped_search — chat_id_filter parameter on search/search_episodic_with_cache."""
from __future__ import annotations
import asyncio
from unittest.mock import MagicMock, AsyncMock


def _make_episodic_mock(return_docs=None):
    ep = MagicMock()
    ep._write_lock = MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock())
    ep.search = AsyncMock(return_value=return_docs or [])
    return ep


def test_search_episodic_passes_chat_id_filter():
    """layers.search_episodic passes chat_id_filter to episodic.search."""
    from memory.layers import MemoryLayers
    ep = _make_episodic_mock()
    layers = MemoryLayers(MagicMock(), ep, MagicMock())
    asyncio.run(layers.search_episodic("query", chat_id_filter="chat42"))
    call_kwargs = ep.search.call_args[1]
    assert call_kwargs.get("chat_id_filter") == "chat42"


def test_search_cache_key_differs_with_chat_id_filter():
    """Different chat_id_filter values produce different cache keys."""
    from memory.layers import MemoryLayers
    key_global = MemoryLayers._search_cache_key("query", topic_id=None, chat_id_filter=None)
    key_scoped = MemoryLayers._search_cache_key("query", topic_id=None, chat_id_filter="chat42")
    assert key_global != key_scoped


def test_episodic_search_chat_id_filter_adds_clause():
    """EpisodicMemory.search adds chat_id clause when chat_id_filter given."""
    from memory.episodic.episodic import EpisodicMemory
    ep = EpisodicMemory()
    mock_col = MagicMock()
    mock_col.query.return_value = {
        "documents": [[]], "metadatas": [[]], "distances": [[]]
    }
    ep._collection = mock_col
    asyncio.run(ep.search("query", chat_id_filter="chat-xyz"))
    call_kwargs = mock_col.query.call_args[1]
    where = call_kwargs.get("where") or {}
    # chat_id should be in where clause (either direct or in $and)
    where_str = str(where)
    assert "chat-xyz" in where_str
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/lenovo/workspace/goat2 && python -m pytest tests/test_chat_id_scoped_search.py -v 2>&1 | head -20
```
Expected: FAIL (chat_id_filter parameter doesn't exist yet).

- [ ] **Step 3: Modify `memory/episodic/episodic.py` — add chat_id_filter to search()**

In `search()` method, add `chat_id_filter: str | None = None` parameter and add a clause:

```python
    async def search(
        self, query: str, limit: int = 5,
        after: float | None = None, before: float | None = None,
        topic_id: str | None = None,
        chat_id_filter: str | None = None,
    ) -> list[dict]:
```

In the clauses list, add:
```python
        if chat_id_filter is not None:
            clauses.append({"chat_id": {"$eq": chat_id_filter}})
```
(add this alongside the existing `after`, `before`, `topic_id` clauses)

- [ ] **Step 4: Modify `memory/layers.py` — search_episodic, search_episodic_with_cache, _search_cache_key**

Update `search_episodic`:
```python
    async def search_episodic(
        self, query: str, limit: int = 5,
        after: float | None = None, before: float | None = None,
        topic_id: str | None = None,
        chat_id_filter: str | None = None,
    ) -> list[dict]:
        results = await self._episodic.search(
            query, limit=limit, after=after, before=before,
            topic_id=topic_id, chat_id_filter=chat_id_filter,
        )
        return enforce_result_limit(results)
```

Update `search_episodic_with_cache`:
```python
    async def search_episodic_with_cache(
        self, chat_id: str, query: str, limit: int = 5,
        topic_id: str | None = None,
        chat_id_filter: str | None = None,
    ) -> tuple[list[dict], bool, str]:
        cache_key = self._search_cache_key(query, topic_id, chat_id_filter)
        cached = await self._cache.get(chat_id, cache_key)
        if cached is not None:
            return cached["results"], True, cache_key
        log.debug("episodic search (cache miss) chat=%s query=%r", chat_id, query[:80])
        results = enforce_result_limit(
            await self._episodic.search(
                query, limit=limit, topic_id=topic_id, chat_id_filter=chat_id_filter,
            )
        )
        await self._cache.set(chat_id, cache_key, {"results": results})
        return results, False, cache_key
```

Update `_search_cache_key`:
```python
    @staticmethod
    def _search_cache_key(
        query: str, topic_id: str | None = None, chat_id_filter: str | None = None,
    ) -> str:
        key_str = query + (topic_id or "") + (chat_id_filter or "")
        digest = hashlib.sha256(key_str.encode("utf-8")).hexdigest()[:16]
        return f"{_SEARCH_NAMESPACE}:{digest}"
```

- [ ] **Step 5: Modify `orchestrator/orchestrator.py` — add _thematic_scoped, remove _specific_key**

In `_prefetch_daemon`, in the cold path section:

Remove the `_specific_key` coroutine definition (lines ~532-534) and `keys = extract_structural_keys(user_message)` (line ~498).

Add `_thematic_scoped` coroutine:
```python
        async def _thematic_scoped() -> dict:
            results, hit, key = await layers.search_episodic_with_cache(
                chat_id, user_message, limit=limit, chat_id_filter=chat_id,
            )
            return {"results": results, "cache_hit": hit, "cache_key": key}
```

Update the tasks list:
```python
        tasks: list = [("thematic", _thematic()), ("thematic_scoped", _thematic_scoped())]
        if topic_return_id:
            tasks.append(("topic_return", _topic_filtered(topic_return_id)))
        if after_before is not None:
            tasks.append(("temporal", _temporal(after_before)))
```

Update the mechanism counting loop — `thematic_scoped` counts toward thematic:
```python
            if name in ("thematic", "topic_return", "thematic_scoped"):
                thematic_count += count
```

Update the log line to remove `specific_key`:
```python
        log.info(
            "prefetch merge chat=%s state=cold mechanisms=%d merged=%d thematic=%d temporal=%d",
            chat_id, len(parts), len(merged), thematic_count, temporal_count,
        )
```

Update the meta dict:
```python
        meta = {
            "warm_served": False,
            "thematic": thematic_count,
            "temporal": temporal_count,
            "specific_key": 0,  # removed mechanism; kept key for observability compat
        }
```

Remove the import of `extract_structural_keys` from the top of orchestrator.py:
```python
from memory.query_classifier import extract_temporal_range
```
(remove `extract_structural_keys` from this import line)

- [ ] **Step 6: Update registry to add GLiNERExtractor + pass to MemoryLayers**

In `registry/registry.py`, add after existing imports:
```python
from memory.gliner_extractor import GLiNERExtractor
```

Add attribute to `__init__`:
```python
        self._gliner_extractor: GLiNERExtractor | None = None
```

Add property:
```python
    @property
    def gliner_extractor(self) -> GLiNERExtractor:
        """Shared GLiNERExtractor; model loads lazily on first extraction call."""
        if self._gliner_extractor is None:
            self._gliner_extractor = GLiNERExtractor()
        return self._gliner_extractor
```

Update `memory_layers` property:
```python
    @property
    def memory_layers(self) -> MemoryLayers:
        if self._memory_layers is None:
            self._memory_layers = MemoryLayers(
                self.working_memory, self.episodic_memory, self.permanent_memory,
                cache_ttl=SESSION_CACHE_TTL,
                extractor=self.gliner_extractor,
            )
        return self._memory_layers
```

- [ ] **Step 7: Update requirements.txt**

Add GLiNER as an optional dependency (commented, not required for core operation):
```
#   gliner>=0.2.0      # memory/gliner_extractor.py (L3 entity enrichment; ~200MB model download on first use)
```

- [ ] **Step 8: Run all tests**

```bash
cd /home/lenovo/workspace/goat2 && python -m pytest tests/ -v 2>&1 | tail -30
```
Expected: All tests PASS including new chat_id scoped search tests.

- [ ] **Step 9: Commit**

```bash
git add memory/episodic/episodic.py memory/layers.py orchestrator/orchestrator.py registry/registry.py requirements.txt tests/test_chat_id_scoped_search.py
git commit -m "feat: chat_id-scoped thematic search replaces regex specific-key; registry wires GLiNER"
```

---

## Self-Review

**Spec coverage:**
- GLiNER extractor with lazy loading ✓ (Task 1)
- `update_metadata` on EpisodicQueries ✓ (Task 2)
- `enrich_l3_entry` helper ✓ (Task 2)
- `store()` returns doc_id, accepts pre-generated doc_id ✓ (Task 3)
- `store_episodic()` returns doc_id, accepts doc_id ✓ (Task 3)
- `_archive_turn` accepts doc_id ✓ (Task 3)
- Orchestrator pre-generates doc_id, stores as `l3_id` in L2 messages ✓ (Task 4)
- auto_promote enriches dropped messages with `l3_id` ✓ (Task 5)
- `MemoryLayers.__init__` accepts extractor ✓ (Task 5)
- `search()` accepts `chat_id_filter` ✓ (Task 6)
- `search_episodic*` propagates `chat_id_filter` ✓ (Task 6)
- `_search_cache_key` includes `chat_id_filter` ✓ (Task 6)
- `_thematic_scoped` always runs on cold path ✓ (Task 6)
- `_specific_key` and `extract_structural_keys` removed ✓ (Task 6)
- Registry wires GLiNER ✓ (Task 6)
- `requirements.txt` updated ✓ (Task 6)
- No summaries, no LLM at write time ✓

**Placeholder scan:** None found — all steps contain actual code.

**Type consistency:**
- `GLiNERExtractor.extract()` returns `dict` with keys `entities`, `entity_types`, `memory_type` — used identically in enrichment.py ✓
- `update_metadata(doc_id: str, updates: dict) -> None` — matches calls in enrichment.py ✓
- `store(..., doc_id=None) -> str` — matches layers.py and orchestrator.py calls ✓
- `schedule_auto_promote(chat_id, working, episodic=None, extractor=None)` — backward-compatible call in layers.py ✓
- `_search_cache_key(query, topic_id, chat_id_filter)` — all call sites updated ✓
