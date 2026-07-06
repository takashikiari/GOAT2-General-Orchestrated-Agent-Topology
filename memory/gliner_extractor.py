"""memory.gliner_extractor — GLiNER-based entity extraction for L3 enrichment."""
from __future__ import annotations

import asyncio

from utils.logging.setup import get_logger

log = get_logger(__name__)

_ENTITY_LABELS = [
    "person", "technology", "project", "credential",
    "location", "organization", "event", "preference",
]

_MODEL_NAME = "urchade/gliner_multi-v2.1"
_gliner_model = None


def _get_shared_model():
    global _gliner_model
    if _gliner_model is None:
        from gliner import GLiNER  # lazy import — not installed by default
        _gliner_model = GLiNER.from_pretrained(_MODEL_NAME)
        log.info("GLiNERExtractor: model loaded (%s)", _MODEL_NAME)
    return _gliner_model


class GLiNERExtractor:
    """Zero-shot NER using GLiNER; shares one module-level model instance."""

    MODEL_NAME = _MODEL_NAME

    def _get_model(self):
        return _get_shared_model()

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
