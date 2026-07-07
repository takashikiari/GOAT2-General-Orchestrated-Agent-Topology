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

