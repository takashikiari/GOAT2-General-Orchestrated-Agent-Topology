"""memory.reranker — registry-owned cross-encoder reranker for prefetch results.

Re-scores the merged candidate list using a multilingual cross-encoder
(sentence-transformers CrossEncoder). Unlike bi-encoder similarity, the
cross-encoder sees (query, document) jointly, giving much higher relevance
precision at the cost of sequential inference. Runs on the top-K candidates
so it stays within the 1.0s prefetch timeout on CPU.

The raw logit is passed through sigmoid → blended_score ∈ (0, 1), keeping
the score range compatible with ``_blended_gap_filter`` in ``memory.layers``.
"""
from __future__ import annotations

import asyncio
import math

from memory.config import RERANKER_MODEL, RERANKER_TOP_K
from utils.logging.setup import get_logger

log = get_logger(__name__)


class CrossEncoderReranker:
    """Registry-owned cross-encoder reranker; model loaded lazily on first use."""

    def __init__(self) -> None:
        """No model loaded at construction — deferred to first rerank call."""
        self._model = None

    def _get_model(self):
        """Return the cross-encoder model, loading it on first call (sync)."""
        if self._model is None:
            from sentence_transformers import CrossEncoder  # lazy — optional dep
            self._model = CrossEncoder(RERANKER_MODEL)
            log.info("CrossEncoderReranker: loaded %s", RERANKER_MODEL)
        return self._model

    def _load_and_prime(self) -> None:
        """Load model + run one dummy prediction to compile the PyTorch JIT graph.

        Without the prediction pass, the first real predict() call during prefetch
        takes ~0.96 s (JIT compilation at 1.04 it/s vs 8.91 it/s on subsequent
        calls) — enough to exceed the 1.0 s prefetch timeout and drop L3 from
        the first response.
        """
        model = self._get_model()
        model.predict([("warmup query", "warmup document")])
        log.info("CrossEncoderReranker: JIT inference path primed")

    async def warmup(self) -> None:
        """Pre-load the model and prime JIT at startup."""
        await asyncio.to_thread(self._load_and_prime)

    async def rerank(
        self, query: str, results: list[dict], top_k: int = RERANKER_TOP_K,
    ) -> list[dict]:
        """Rerank up to top_k candidates; blended_score overwritten with sigmoid(logit).

        Args:
            query: User message used as the query side of each (query, doc) pair.
            results: Candidate results from hybrid retrieval; blended_score set.
            top_k: Number of candidates to score (capped to len(results)).
        Returns:
            Results sorted by cross-encoder score descending; blended_score is now
            sigmoid(raw_logit) ∈ (0, 1) so the gap filter in assemble_context
            sees a well-scaled distribution regardless of upstream score sources.
        """
        if not results:
            return results
        candidates = results[:top_k]
        pairs = [(query, r["content"]) for r in candidates]
        model = self._get_model()

        def _predict() -> list[float]:
            return model.predict(pairs).tolist()

        raw_scores: list[float] = await asyncio.to_thread(_predict)
        out: list[dict] = []
        for r, raw in zip(candidates, raw_scores):
            r = dict(r)
            r["blended_score"] = 1.0 / (1.0 + math.exp(-float(raw)))
            out.append(r)
        out.sort(key=lambda r: r["blended_score"], reverse=True)
        return out
