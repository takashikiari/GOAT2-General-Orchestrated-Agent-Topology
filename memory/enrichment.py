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
