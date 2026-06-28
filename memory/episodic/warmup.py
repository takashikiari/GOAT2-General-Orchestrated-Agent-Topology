"""memory.episodic.warmup — startup pre-warm for the episodic ChromaDB collection.

Separated from ``episodic.py`` to keep that module under the file-size limit and
because warmup is a lifecycle concern, not CRUD. Called once at startup
(``EpisodicMemory.warmup``) so the first real per-turn search doesn't hit the
0.5s prefetch timeout on a cold collection + embedding model.
"""
from __future__ import annotations

import asyncio

from memory.config import EPISODIC_COLLECTION_NAME
from utils.logging.setup import get_logger

log = get_logger(__name__)
__all__ = ["warmup_collection"]

# A throwaway query text — content is irrelevant; only the embedding + collection
# initialisation side effect matters.
_WARMUP_QUERY = " "


async def warmup_collection(get_collection) -> None:
    """Run a 1-result throwaway query to force collection + embedding init.

    Best-effort: failures are logged, never raised — warmup is an optimisation,
    not a gate; a failed warmup simply retries on the first real use.

    Args:
        get_collection: ``EpisodicMemory._get_collection`` (lazy collection
            builder). Passed in so this helper stays decoupled from the class.
    """
    try:
        await asyncio.to_thread(
            get_collection().query, query_texts=[_WARMUP_QUERY], n_results=1,
        )
        log.info("EpisodicMemory warmed up (collection=%s)", EPISODIC_COLLECTION_NAME)
    except Exception as exc:  # noqa: BLE001 — warmup must never block startup
        log.warning("EpisodicMemory warmup failed (will retry on first use): %s", exc)