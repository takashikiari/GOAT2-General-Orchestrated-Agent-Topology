"""memory.gliner_extractor — GLiNER-based entity extraction for L3 enrichment."""
from __future__ import annotations

import asyncio
import threading

from utils.logging.setup import get_logger

log = get_logger(__name__)

_ENTITY_LABELS = [
    "person", "technology", "project", "credential",
    "location", "organization", "event", "preference",
    "date", "time",
]

_MODEL_NAME = "urchade/gliner_multi-v2.1"
_MAX_WORDS = 100  # Romanian text averages ~3 wordpiece tokens/word; 100 words ≈ 300 tokens, under the 384 limit
_gliner_model = None
# Protects the module-level singleton from concurrent asyncio.to_thread loads.
# Double-checked locking: outer check avoids lock contention on the hot path
# (model already loaded); inner check prevents double-load when two threads
# both see None before the first finishes from_pretrained() (~10 s).
_model_lock = threading.Lock()


def _get_shared_model():
    global _gliner_model
    if _gliner_model is None:
        with _model_lock:
            if _gliner_model is None:
                from gliner import GLiNER  # lazy import — not installed by default
                _gliner_model = GLiNER.from_pretrained(_MODEL_NAME)
                log.info("GLiNERExtractor: model loaded (%s)", _MODEL_NAME)
    return _gliner_model


def _chunk_text(text: str) -> list[str]:
    words = text.split()
    if len(words) <= _MAX_WORDS:
        return [text]
    return [" ".join(words[i:i + _MAX_WORDS]) for i in range(0, len(words), _MAX_WORDS)]


class GLiNERExtractor:
    """Zero-shot NER using GLiNER; shares one module-level model instance."""

    MODEL_NAME = _MODEL_NAME

    def _get_model(self):
        return _get_shared_model()

    def _extract_sync(self, text: str) -> dict:
        model = self._get_model()
        chunks = _chunk_text(text)
        all_entities: list[str] = []
        all_types: list[str] = []
        for chunk in chunks:
            raw = model.predict_entities(chunk, _ENTITY_LABELS, threshold=0.5)
            all_entities.extend(e["text"] for e in raw)
            all_types.extend(e["label"] for e in raw)
        seen: set[str] = set()
        entities, entity_types = [], []
        for e, t in zip(all_entities, all_types):
            if e not in seen:
                seen.add(e)
                entities.append(e)
                entity_types.append(t)
        memory_type = _infer_type(entities, entity_types, text)
        return {"entities": entities, "entity_types": entity_types, "memory_type": memory_type}

    def _load_and_prime(self) -> None:
        """Load model + run one dummy inference to compile the PyTorch JIT graph.

        Without the inference pass, the first real predict_entities() call in the
        prefetch pipeline takes ~1-3 s (JIT compilation) even after the model
        weights are in memory — enough to blow the 1.0 s prefetch timeout.
        """
        model = _get_shared_model()
        model.predict_entities("warmup", _ENTITY_LABELS, threshold=0.5)
        log.info("GLiNERExtractor: JIT inference path primed")

    async def warmup(self) -> None:
        """Pre-load the GLiNER model and prime JIT at startup."""
        await asyncio.to_thread(self._load_and_prime)

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
