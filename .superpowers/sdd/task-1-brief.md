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

