"""Hints — short text snippets the LLM can use as soft guidance.

Pure Python, no LLM, no regex. Two sources of hints:

  1. Behavioral corrections (``corrections.recall_corrections``).
  2. Static operational hints the codebase owner wants always
     present (loaded from ``config/goat.toml [hints]``).

USAGE:
    from supervisor.mechanisms.hints import build_hints

    hints: list[str] = await build_hints(mm, intent, registry)
    # → [relevant_corrections...] + [static_hints...]

BUG-010 fix:
    ``build_hints`` previously accepted an ``intent`` argument and
    silently discarded it. The same correction hints were returned
    regardless of what the user asked. The new implementation
    filters the recalled corrections by token overlap with the
    user's intent — hints that share zero tokens with the current
    intent are dropped (a "use bullet points" correction is not
    relevant when the user is asking "route this to coder").
    Static hints remain always-on (they describe the system itself,
    not user preferences).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from supervisor.mechanisms.corrections import recall_corrections

if TYPE_CHECKING:
    from config.registry import ServiceRegistry
    from memory.shared import MemoryManager

log = logging.getLogger("goat2.supervisor.mechanisms.hints")

__all__ = ["build_hints", "load_static_hints"]

# Section name in config/goat.toml. Each entry is one hint line.
_HINTS_SECTION: str = "hints"

# Minimum fraction of intent tokens that must appear in a hint for
# it to be considered relevant. Tunable via config/goat.toml [hints]
# in a follow-up; the default is conservative so a short intent
# still keeps obviously-related hints while clearly-unrelated ones
# are dropped.
_INTENT_OVERLAP_THRESHOLD: float = 0.20


def load_static_hints(registry: "ServiceRegistry | None") -> list[str]:
    """Read ``[hints]`` lines from config/goat.toml.

    Returns a list of trimmed, non-empty strings. Missing file
    or section → empty list. The list preserves toml order.

    Args:
        registry: ServiceRegistry (for config access); ignored
            if absent — the function falls back to ``[]``.
    """
    if registry is None:
        return []
    try:
        from config.modular_loader import load_goat_config
        data = load_goat_config() or {}
        section = data.get(_HINTS_SECTION, {})
        if not isinstance(section, dict):
            return []
        return [str(v).strip() for v in section.values() if str(v).strip()]
    except Exception as exc:  # noqa: BLE001
        log.debug("load_static_hints: toml load failed: %s", exc)
        return []


def _intent_tokens(intent: str) -> set[str]:
    """Lower-cased token set for ``intent``.

    Pure split — no stopwords, no stemming. The threshold check
    is intentionally lenient because we want at least loose
    semantic relevance, not exact match.
    """
    return {tok for tok in (intent or "").lower().split() if len(tok) >= 3}


def _filter_by_intent(hints: list[str], intent: str) -> list[str]:
    """Keep hints whose token overlap with ``intent`` is above the
    configured threshold. When ``intent`` is empty, all hints pass
    through unchanged (so the caller never sees a completely empty
    list because the user's input was minimal)."""
    intent_tokens = _intent_tokens(intent)
    if not intent_tokens:
        return list(hints)
    kept: list[str] = []
    for hint in hints:
        hint_tokens = {tok for tok in hint.lower().split() if len(tok) >= 3}
        if not hint_tokens:
            # Hint has no usable tokens (e.g. just punctuation) —
            # keep it so we don't accidentally filter everything.
            kept.append(hint)
            continue
        # Count hint tokens that appear in the intent (and vice versa).
        overlap = len(hint_tokens & intent_tokens)
        # Compare against the larger of the two sets so a very short
        # intent doesn't penalise a long hint, and vice versa.
        denom = max(len(hint_tokens), len(intent_tokens))
        ratio = overlap / denom if denom else 0.0
        if ratio >= _INTENT_OVERLAP_THRESHOLD or overlap >= 1:
            kept.append(hint)
        else:
            log.debug("build_hints: filtered unrelated hint (ratio=%.2f): %s",
                      ratio, hint[:60])
    return kept


async def build_hints(
    mm: "MemoryManager | None",
    intent: str,
    registry: "ServiceRegistry | None" = None,
    limit: int = 3,
) -> list[str]:
    """Assemble the full hint list for this turn.

    Order: behaviorally-relevant corrections first (highest signal),
    then static operational hints (always-on, not filtered).

    Args:
        mm: Registry's MemoryManager (or None).
        intent: Raw user intent for this turn. Used to filter
            corrections by token overlap (BUG-010).
        registry: ServiceRegistry for static-hint lookup.
        limit: Max number of correction hints to recall.

    Returns:
        List of hint strings. Always a list; never raises.
    """
    out: list[str] = []
    try:
        recalled = await recall_corrections(mm, limit=limit)
    except Exception as exc:  # noqa: BLE001
        log.debug("build_hints: corrections failed: %s", exc)
        recalled = []
    out.extend(_filter_by_intent(recalled, intent))
    out.extend(load_static_hints(registry))
    return out