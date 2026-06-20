"""Layer rendering — three-layer output formatter.

Extracted from ``supervisor/session/mem_inject.py`` to keep
that file under the 260-line ceiling. This module owns:

  - The three layer labels (``[Present]``, ``[Present-Past]``,
    ``[Past]``).
  - The per-layer entry cap logic.
  - The format-entries-with-age wiring for each layer.
  - The persona block rendering for ``[Past]``.

Pure formatting — no I/O, no LLM, no memory-manager calls.
The orchestrator in ``mem_inject.py`` fetches the data and
hands it to the helpers here.

USAGE:
    from supervisor.session.layer_renderer import (
        render_present_layer, render_present_past_layer,
        render_past_layer,
    )
    block = render_present_layer(records, now=time.time())
"""
from __future__ import annotations

import json
import logging
from typing import Final

from memory.temporal.temporal_format import (
    format_entries_with_age,
    load_temporal_config,
)
from supervisor.session.action_log_renderer import (
    render_action_log_section,
    split_records_by_type,
)

log = logging.getLogger("goat2.supervisor.session.layer_renderer")

__all__ = [
    "LAYER_PRESENT",
    "LAYER_PRESENT_PAST",
    "LAYER_PAST",
    "PAST_UNAVAILABLE_MARKER",
    "render_present_layer",
    "render_present_past_layer",
    "render_past_layer",
    "format_episodic_hit",
]


LAYER_PRESENT:      Final[str] = "[Present]"
LAYER_PRESENT_PAST: Final[str] = "[Present-Past]"
LAYER_PAST:         Final[str] = "[Past]"

# Sentinel inserted into [Past] when Letta is unreachable, so
# the LLM sees the layer exists but knows the persona data
# is missing.
PAST_UNAVAILABLE_MARKER: Final[str] = "[unavailable]"

# Module-level cache for the relative-age label config
# (BUG-003). Loaded once at import.
_TEMPORAL_CFG = load_temporal_config()


def _render_age(records: list, *, now: float, max_content_len: int = 200) -> str:
    """Render a list of records using the standard age-labelled
    format. Returns an empty string when the list is empty."""
    if not records:
        return ""
    return format_entries_with_age(
        records,
        max_content_len=max_content_len,
        now=now,
        cfg=_TEMPORAL_CFG,
    )


def render_present_layer(
    records: list,
    *,
    now: float,
    max_entries: int,
) -> str:
    """Render the [Present] block.

    Format::

        [Present] (N)
        Last turn actions:
          - memory_delete(key=X) → FAIL: Key not found
          - memory_get(key=Y) → ok: Content: ...
        - [Xs ago] [working] turn:K: content
        ...

    Records whose key ends with ``:actions`` are rendered
    FIRST under a ``Last turn actions:`` header using the
    structured format from ``format_action_log``. This gives
    the model a concrete action record (with success / failure
    markers) for self-reporting questions like "what did you
    do last turn?" — without the model having to confabulate
    from its own previous text.

    Args:
        records: Fresh working-memory records (age < present_max_age_s).
        now: Reference time for age computation.
        max_entries: Hard cap on lines rendered (sum of action
            log lines + other entries).

    Returns:
        The rendered block, or just the header when the layer
        is empty (so the LLM sees the layer exists).
    """
    # Split: action-log records vs everything else. Action logs
    # render FIRST so the model sees structured data before any
    # free-text record.
    action_records, other_records = split_records_by_type(records)

    lines: list[str] = []

    # Action log block (structured, success/failure markers).
    # Only the most recent action log is rendered — older logs
    # would crowd the prompt without adding signal.
    if action_records:
        last_content = (
            action_records[0].get("content")
            if isinstance(action_records[0], dict)
            else getattr(action_records[0], "content", "")
        ) or ""
        action_section = render_action_log_section(last_content)
        if action_section:
            lines.append(action_section)

    # Other working-memory entries (the standard age-labelled block).
    body = _render_age(other_records[:max_entries], now=now)
    if body:
        lines.append(body)

    if not lines:
        return LAYER_PRESENT
    total_entries = len(other_records[:max_entries]) + (1 if action_records else 0)
    return f"{LAYER_PRESENT} ({total_entries})\n" + "\n".join(lines)


def format_episodic_hit(hit, *, max_content_len: int = 200) -> str:
    """Format one episodic recall hit as ``- [episodic] content``.

    Accepts any object exposing ``.content`` (MemoryEntry
    shape) or being a plain string. Returns an empty string
    if the hit carries no usable content.
    """
    content = getattr(hit, "content", None)
    if content is None and isinstance(hit, str):
        content = hit
    content = (content or "").strip()
    if not content:
        return ""
    return f"- [episodic] {content[:max_content_len]}"


def render_present_past_layer(
    working_records: list,
    episodic_hits: list,
    *,
    now: float,
    max_entries: int,
) -> str:
    """Render the [Present-Past] block — working recent + episodic.

    The combined cap (``max_entries``) is shared between both
    sources; episodic hits get the budget first, working
    memory fills the remainder.

    Format::

        [Present-Past] (M)
        - [Xs ago] [working] turn:K: content
        - [episodic] hit-1
        - [episodic] hit-2

    Args:
        working_records: Working-memory records in the
            present-past age range.
        episodic_hits: Episodic recall hits (already capped
            by the caller if needed).
        now: Reference time for age computation.
        max_entries: Hard cap on lines rendered across both
            sources.

    Returns:
        The rendered block, or just the header when empty.
    """
    # Caller has already capped episodic at episodic_top_k.
    # The combined cap further limits working memory.
    n_episodic = len(episodic_hits)
    working_budget = max(0, max_entries - n_episodic)
    working_capped = working_records[:working_budget]

    lines: list[str] = [LAYER_PRESENT_PAST]
    body = _render_age(working_capped, now=now)
    if body:
        lines.append(body)
    for hit in episodic_hits:
        formatted = format_episodic_hit(hit)
        if formatted:
            lines.append(formatted)
    if len(lines) == 1:
        # Body was empty and no episodic hits either.
        return LAYER_PRESENT_PAST
    return "\n".join(lines)


def render_past_layer(
    persona_text: str,
    past_records: list,
    *,
    now: float,
    max_entries: int,
) -> str:
    """Render the [Past] block — Letta persona + old working memory.

    The persona block is rendered FIRST (user identity, long-
    term preferences) so the LLM sees who the user is before
    old conversation history.

    Format::

        [Past]
        - persona: <Letta persona block>
        - [Xd ago] [working] turn:K: content
        ...

    Args:
        persona_text: Raw Letta persona block (or empty string
            when Letta is unavailable).
        past_records: Old working-memory records.
        now: Reference time for age computation.
        max_entries: Hard cap on working-memory lines.

    Returns:
        The rendered block.
    """
    lines: list[str] = [LAYER_PAST]
    if persona_text:
        lines.append(f"- persona: {persona_text}")
    else:
        lines.append(f"- persona: {PAST_UNAVAILABLE_MARKER}")
    body = _render_age(past_records[:max_entries], now=now)
    if body:
        lines.append(body)
    return "\n".join(lines)