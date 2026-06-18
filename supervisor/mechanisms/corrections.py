"""Behavioral corrections recall — surface past user corrections
as soft hints the LLM can weight against repeating the same
mistake.

Pure orchestration of the memory manager's episodic search —
no LLM, no regex. The mechanism itself is async (it awaits the
memory manager) but does no language modeling.

USAGE:
    from supervisor.mechanisms.corrections import recall_corrections

    hints: list[str] = await recall_corrections(mm, limit=3)
    # → ["intent=\"...\" → goat=router, user wanted: ...", ...]

QUERY STRATEGY:
    The mechanism does a semantic search over the episodic
    (ChromaDB) tier with a fixed prompt. Past corrections are
    stored as JSON payloads ``{"intent", "goat_routed",
    "user_wanted"}``; ``recall_corrections`` parses them and
    emits human-readable hint strings.

FAILURE MODES:
    - mm is None → ``[]``
    - mm.episodic.search missing → ``[]``
    - JSON unparseable → fallback to first 200 chars of content
    - Any exception → ``[]`` (defensive — never block the turn)
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from memory.shared import MemoryManager

log = logging.getLogger("goat2.supervisor.mechanisms.corrections")

__all__ = ["QUERY", "recall_corrections"]

# Static query used to surface past corrections via semantic
# search. Phrased to match the natural-language topic of the
# correction records.
QUERY: str = "user correction routing preference"

# Default cap when caller doesn't specify.
DEFAULT_LIMIT: int = 3


async def recall_corrections(
    mm: "MemoryManager | None",
    limit: int = DEFAULT_LIMIT,
) -> list[str]:
    """Return up to ``limit`` short human-readable correction hints.

    Args:
        mm: The registry's ``MemoryManager`` (or None).
        limit: Maximum number of hints to return.

    Returns:
        List of hint strings, one per correction. Empty list on
        any failure or when mm is None.
    """
    if mm is None:
        return []
    try:
        episodic = getattr(mm, "episodic", None)
        if episodic is None or not hasattr(episodic, "search"):
            return []
        results = await episodic.search(QUERY, limit=limit)
    except Exception as exc:  # noqa: BLE001 — never block on memory
        log.debug("recall_corrections: episodic search failed: %s", exc)
        return []
    hints: list[str] = []
    for r in results or []:
        doc = r.get("content") if isinstance(r, dict) else None
        if not doc:
            continue
        payload: object | None = None
        if isinstance(doc, str):
            try:
                payload = json.loads(doc)
            except (TypeError, ValueError):
                payload = None
        elif isinstance(doc, dict):
            payload = doc
        if isinstance(payload, dict):
            intent  = str(payload.get("intent", "?"))[:80]
            goat    = str(payload.get("goat_routed", "?"))
            wanted  = str(payload.get("user_wanted", "?"))[:80]
            hints.append(f"intent=\"{intent}\" → goat={goat}, user wanted: {wanted}")
        elif isinstance(doc, str):
            hints.append(doc[:200])
    return hints