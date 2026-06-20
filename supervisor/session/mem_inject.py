"""Memory injection — three-layer memory orchestrator.

USAGE:
    from supervisor.session.mem_inject import mem_turn

    ctx = await mem_turn(mm, intent)
    # → 3-labelled blocks: [Present] / [Present-Past] / [Past]

STRUCTURE (Faza 2):
  - [Present]        — working memory, age < present_max_age_s.
  - [Present-Past]   — working memory + episodic recall top-K.
  - [Past]           — old working memory + Letta persona block.

All thresholds come from ``config/memory.toml [temporal_layers]``
via the canonical defaults in ``config.limits``. Layer rendering
is delegated to ``supervisor.session.layer_renderer``; data
fetching to ``supervisor.session.memory_helpers``.

This module is the public API and the orchestrator only.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from memory.shared import MemoryManager

from config.limits import (
    DEFAULT_EPISODIC_TOP_K,
    DEFAULT_TEMPORAL_PAST_MAX_ENTRIES,
    DEFAULT_TEMPORAL_PRESENT_MAX_AGE_S,
    DEFAULT_TEMPORAL_PRESENT_MAX_ENTRIES,
    DEFAULT_TEMPORAL_PRESENT_PAST_MAX_AGE_S,
    DEFAULT_TEMPORAL_PRESENT_PAST_MAX_ENTRIES,
)
from supervisor.mechanisms.namespace import is_dag_key
from supervisor.session.layer_renderer import (
    LAYER_PAST,
    LAYER_PRESENT,
    LAYER_PRESENT_PAST,
    render_past_layer,
    render_present_layer,
    render_present_past_layer,
)
from supervisor.session.memory_helpers import (
    bucket_by_age,
    fetch_episodic_hits,
    fetch_persona,
    filter_dag,
    list_working,
)

__all__ = [
    "mem_turn",
    "recall_context",
    "working_memory_block",
    # Layer labels (re-exported for backward compatibility).
    "LAYER_PRESENT",
    "LAYER_PRESENT_PAST",
    "LAYER_PAST",
    # Threshold constants (re-exported for tests).
    "_present_max_age_s",
    "_present_past_max_age_s",
    "_present_max_entries",
    "_present_past_max_entries",
    "_past_max_entries",
    "_episodic_top_k",
]

log = logging.getLogger("goat2.supervisor.session.mem_inject")


# ── Tunables (canonical home: config.limits) ──────────────────────────────
_present_max_age_s:         Final[int] = DEFAULT_TEMPORAL_PRESENT_MAX_AGE_S
_present_past_max_age_s:    Final[int] = DEFAULT_TEMPORAL_PRESENT_PAST_MAX_AGE_S
_present_max_entries:       Final[int] = DEFAULT_TEMPORAL_PRESENT_MAX_ENTRIES
_present_past_max_entries:  Final[int] = DEFAULT_TEMPORAL_PRESENT_PAST_MAX_ENTRIES
_past_max_entries:          Final[int] = DEFAULT_TEMPORAL_PAST_MAX_ENTRIES
_episodic_top_k:            Final[int] = DEFAULT_EPISODIC_TOP_K


# ── Backward-compatible constants ──────────────────────────────────────────
_NO_MEMORY_FALLBACK: Final[str] = "[Memory: UNAVAILABLE]"
_RECALL_LIMIT:       Final[int] = 5
_WM_LIMIT:           Final[int] = 50


# ── Legacy public API ─────────────────────────────────────────────────────


async def working_memory_block(
    mm: "MemoryManager | None",
    *,
    include_dag: bool = False,
    intent: str = "",
) -> str:
    """Render the legacy single-block working-memory view.

    Preserved for backward compatibility with callers that
    don't use the three-layer structure (e.g. tests, MCP
    diagnose_turn). The three-layer ``mem_turn`` is the
    canonical entry point for the GOAT prompt.
    """
    if mm is None:
        return ""
    try:
        records = await list_working(mm)
        records = await filter_dag(records, include_dag)
        from supervisor.mechanisms.context_builder import build_context
        return build_context(records, intent=intent, now=time.time())
    except Exception as exc:  # noqa: BLE001
        log.debug("working_memory_block failed: %s", exc)
        return ""


async def recall_context(
    mm: "MemoryManager | None",
    query: str,
    *,
    include_dag: bool = False,
    intent: str = "",
) -> str:
    """Return the legacy combined ``[Memory] + [Working Memory]`` block.

    Preserved for backward compatibility. The three-layer
    ``mem_turn`` supersedes this for the GOAT prompt.
    """
    if mm is None:
        return _NO_MEMORY_FALLBACK
    mem_block = ""
    wm_block = ""
    try:
        hits = await mm.recall(SESSION_ROLE, query, limit=_RECALL_LIMIT)
        lines = [h.content.strip() for h in hits if h.content.strip()]
        if lines:
            mem_block = "[Memory]\n" + "\n".join(f"- {ln}" for ln in lines)
    except Exception as exc:  # noqa: BLE001
        log.debug("recall_context fan-out failed: %s", exc)
    try:
        wm_block = await working_memory_block(
            mm, include_dag=include_dag, intent=intent,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("recall_context wm failed: %s", exc)
    blocks = [b for b in (mem_block, wm_block) if b]
    return "\n".join(blocks) if blocks else _NO_MEMORY_FALLBACK


# ── Three-layer orchestrator ───────────────────────────────────────────────


async def mem_turn(
    mm: "MemoryManager | None",
    intent: str,
    *,
    turn_number: int = 0,
) -> str:
    """Assemble the three-layer memory context for this turn.

    The output is a single string with three labelled sections:

      [Present] (N)
        - [Xs ago] [working] turn:K: content
      [Present-Past] (M)
        - [Xs ago] [working] turn:K: content
        - [episodic] hit-1
      [Past]
        - persona: <Letta persona block>
        - [Xd ago] [working] turn:K: content

    Args:
        mm: MemoryManager (or None → ``[Memory: UNAVAILABLE]``).
        intent: Raw user intent. Used as the episodic recall
            query.
        turn_number: Number of completed turns before this one
            (i.e. ``len(history.messages)`` BEFORE the current
            user turn is buffered). Used as the episodic recall
            cache bucket key so retry / re-render within the
            same turn is cheap and parallel turns don't collide.
            Defaults to ``0`` for backward compatibility with
            callers that don't track history.

    Returns:
        The rendered three-layer block.
    """
    if mm is None:
        return _NO_MEMORY_FALLBACK

    now = time.time()

    # 1. Fetch all working-memory records (delegated).
    try:
        all_records = await list_working(mm)
        all_records = await filter_dag(all_records, include_dag=False)
    except Exception as exc:  # noqa: BLE001
        log.debug("mem_turn: list_working failed: %s", exc)
        all_records = []

    # 2. Bucket by age.
    present, present_past, past = bucket_by_age(
        all_records,
        now=now,
        present_max_age_s=_present_max_age_s,
        present_past_max_age_s=_present_past_max_age_s,
    )

    # 3. Render [Present].
    present_block = render_present_layer(
        present, now=now, max_entries=_present_max_entries,
    )

    # 4. Fetch episodic recall hits (timeout-protected + cached).
    episodic_hits = await fetch_episodic_hits(
        mm, intent, _episodic_top_k, turn_number=turn_number,
    )
    episodic_hits = episodic_hits[:_episodic_top_k]

    # 5. Render [Present-Past].
    present_past_block = render_present_past_layer(
        present_past, episodic_hits,
        now=now, max_entries=_present_past_max_entries,
    )

    # 6. Fetch persona + render [Past].
    persona_text = await fetch_persona(mm)
    past_block = render_past_layer(
        persona_text, past,
        now=now, max_entries=_past_max_entries,
    )

    # 7. Assemble. All three layers are always rendered so the
    #    LLM sees the structure exists.
    return "\n\n".join([present_block, present_past_block, past_block])