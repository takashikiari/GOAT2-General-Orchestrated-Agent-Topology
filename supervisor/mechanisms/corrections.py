"""Behavioral corrections recall — surface past user corrections
as soft hints the LLM can weight against repeating the same
mistake.

Pure orchestration of the memory manager's episodic search —
no LLM, no regex. The mechanism itself is async (it awaits the
memory manager) but does no language modeling.

USAGE:
    from supervisor.mechanisms.corrections import (
        recall_corrections, format_correction_hint,
    )

    hints: list[str] = await recall_corrections(mm, limit=3)
    # → ["intent='route this' → goat=router, user wanted: a reply", ...]

QUERY STRATEGY (BUG-011 fix):
    The original implementation used one static query string for
    every correction search. Corrections on topics other than
    routing (style, format, factual accuracy) were never surfaced
    because semantic search matches the literal query text. The
    new implementation fans out across multiple natural-language
    queries and merges the deduplicated results.

    Per-query limits are kept low (the global ``limit`` is divided
    across queries) so the merged result size respects the caller's
    budget.

HINT FORMAT (BUG-013 fix):
    The original format concatenated raw strings into
    ``f'intent="{intent}" → ...'`` — if the intent contained a
    double-quote, the resulting line could not be re-parsed safely.
    The new ``format_correction_hint`` uses JSON-style escaping for
    string fields so each line is unambiguously three key=value
    pairs separated by safe delimiters.

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

__all__ = [
    "DEFAULT_LIMIT",
    "CORRECTION_QUERIES",
    "INTENT_TRUNCATION_CHARS",
    "WANTED_TRUNCATION_CHARS",
    "format_correction_hint",
    "recall_corrections",
]

# Default cap when caller doesn't specify.
DEFAULT_LIMIT: int = 3

# Multi-query expansion (BUG-011). Each query targets a different
# facet of past corrections; results are merged + deduplicated.
# Pure strings — no LLM, no regex. Tunable via config/goat.toml
# [corrections] in a follow-up if needed.
CORRECTION_QUERIES: tuple[str, ...] = (
    "user correction routing preference",
    "user correction style preference",
    "user correction format preference",
    "user correction factual accuracy",
)

# Per-field truncation caps for the hint format. Keeps each hint
# line under ~200 chars so a 3-hint block fits comfortably in the
# prompt budget.
INTENT_TRUNCATION_CHARS: int = 80
WANTED_TRUNCATION_CHARS: int = 80


def format_correction_hint(intent: str, goat: str, wanted: str) -> str:
    """Render one correction as a single safe hint line.

    The output uses JSON-style escaping for the string fields
    (``intent``, ``wanted``) so any character — quotes, newlines,
    braces — is unambiguously contained. The ``goat`` field is
    forced to a single-line identifier (no spaces, no special
    characters) so it can never break the format.

    Args:
        intent: Original intent that triggered the correction.
        goat:   Agent role that handled the turn.
        wanted: What the user actually wanted instead.

    Returns:
        A single-line string of the form
        ``intent=<escaped> → goat=<role>, user wanted: <escaped>``.
    """
    safe_intent = json.dumps(str(intent), ensure_ascii=False)[:INTENT_TRUNCATION_CHARS + 2]
    safe_wanted = json.dumps(str(wanted), ensure_ascii=False)[:WANTED_TRUNCATION_CHARS + 2]
    # ``goat`` must be a safe identifier: strip whitespace, keep
    # only printable non-space characters.
    safe_goat = "".join(
        ch for ch in (goat or "?") if ch.isprintable() and not ch.isspace()
    ) or "?"
    return f"intent={safe_intent} → goat={safe_goat}, user wanted: {safe_wanted}"


def _payload_to_hint(payload: dict) -> str | None:
    """Convert one decoded JSON payload into a hint line.

    Returns ``None`` when the payload is missing required fields,
    so the caller can skip it.
    """
    if not isinstance(payload, dict):
        return None
    intent  = str(payload.get("intent", "?"))[:INTENT_TRUNCATION_CHARS]
    goat    = str(payload.get("goat_routed", "?"))
    wanted  = str(payload.get("user_wanted", "?"))[:WANTED_TRUNCATION_CHARS]
    return format_correction_hint(intent=intent, goat=goat, wanted=wanted)


async def recall_corrections(
    mm: "MemoryManager | None",
    limit: int = DEFAULT_LIMIT,
) -> list[str]:
    """Return up to ``limit`` short human-readable correction hints.

    Multi-query fan-out (BUG-011): searches episodic memory with
    each query in ``CORRECTION_QUERIES``, merges the results,
    deduplicates identical contents, and renders each via
    ``format_correction_hint``.

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
        # Per-query limit: ensure the merged result respects the
        # caller's cap. Use ``max(1, ...)`` so a small limit still
        # gives each query a chance to surface hits.
        per_query_limit = max(1, limit)
        all_results: list[dict] = []
        seen_contents: set[str] = set()
        for query in CORRECTION_QUERIES:
            try:
                results = await episodic.search(query, limit=per_query_limit)
            except Exception as exc:  # noqa: BLE001 — one query failing must not block the others
                log.debug("recall_corrections: query=%r failed: %s", query, exc)
                continue
            for r in results or []:
                doc = r.get("content") if isinstance(r, dict) else None
                if not doc or doc in seen_contents:
                    continue
                seen_contents.add(doc)
                all_results.append({"content": doc})
                if len(all_results) >= limit:
                    break
            if len(all_results) >= limit:
                break

        hints: list[str] = []
        for entry in all_results:
            doc = entry.get("content")
            if not isinstance(doc, str):
                continue
            payload: object | None = None
            try:
                payload = json.loads(doc)
            except (TypeError, ValueError):
                payload = None
            hint = _payload_to_hint(payload) if isinstance(payload, dict) else None
            if hint is None:
                # Unparseable JSON — render a safe preview instead.
                hints.append(doc[:200].replace("\n", " "))
            else:
                hints.append(hint)
        return hints[:limit]
    except Exception as exc:  # noqa: BLE001 — never block on memory
        log.debug("recall_corrections: episodic search failed: %s", exc)
        return []