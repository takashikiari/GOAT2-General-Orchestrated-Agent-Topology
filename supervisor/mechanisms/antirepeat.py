"""Anti-repetition — history dedup + post-call echo check.

Pure Python, no LLM, no embeddings, no regex. Jaccard similarity
over word tokens (lowercased, split on Unicode word boundaries
via Python's ``str.split``-equivalent — no ``re`` module needed).

USAGE:
    from supervisor.mechanisms.antirepeat import dedup_history, is_repetitive

    cleaned = dedup_history(messages)             # before LLM call
    if is_repetitive(response, history):          # after LLM call
        tag = "repetitive"

TWO MECHANISMS:
  - ``dedup_history``: collapses consecutive assistant messages
    whose Jaccard ≥ ``_DEDUP_OVERLAP_THRESHOLD`` (0.90). Keeps
    the latest version. Removes the previous assistant turn from
    the prompt so the LLM doesn't see its own prior output as a
    template to copy.
  - ``is_repetitive``: returns True when the new response
    overlaps ≥ ``_REPETITIVE_THRESHOLD`` (0.85) of any of the last
    ``_REPETITIVE_LOOKBACK`` (3) assistant turns. Tag the
    response so downstream channels (Telegram/CLI) can
    deprioritize it. We do NOT regenerate — that would add an
    LLM call.

Both are sub-millisecond on typical 100-word turns.
"""
from __future__ import annotations

from typing import Final

__all__ = [
    "DEDUP_OVERLAP_THRESHOLD",
    "REPETITIVE_THRESHOLD",
    "REPETITIVE_LOOKBACK",
    "dedup_history",
    "is_repetitive",
]

# Tunable thresholds. Conservative defaults: dedup is permissive
# (0.90 → only near-duplicates), anti-repetition is sensitive
# (0.85 → catch a strong echo). Override at import time if your
# workload demands it.
DEDUP_OVERLAP_THRESHOLD: Final[float] = 0.90
REPETITIVE_THRESHOLD:     Final[float] = 0.85
REPETITIVE_LOOKBACK:      Final[int]   = 3


def _tokenize(text: str) -> set[str]:
    """Word-token set for Jaccard. Lower-cased, no regex.

    ``str.split()`` without arguments splits on any Unicode
    whitespace and discards empties — sufficient for word-level
    Jaccard. O(len(text)). No ``re`` import.
    """
    return {tok.lower() for tok in (text or "").split() if tok}


def _jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard similarity: |A ∩ B| / |A ∪ B|. 0.0 when both empty."""
    if not a and not b:
        return 0.0
    union = len(a | b)
    if not union:
        return 0.0
    return len(a & b) / union


def dedup_history(messages: list[dict]) -> list[dict]:
    """Collapse consecutive near-duplicate assistant messages.

    Walks the message list once. For each assistant message,
    compares its word-token Jaccard against the most recent kept
    assistant. If overlap ≥ ``DEDUP_OVERLAP_THRESHOLD``, the new
    message *replaces* the kept one (we want the latest, in case
    the LLM corrected itself). User/system messages pass through
    untouched. Returns a new list; never mutates the input.
    """
    out: list[dict] = []
    for m in messages or []:
        role = m.get("role") if isinstance(m, dict) else None
        content = m.get("content") if isinstance(m, dict) else ""
        if role != "assistant":
            out.append(m)
            continue
        new_tokens = _tokenize(content)
        replaced = False
        # Only compare against the consecutive run of assistants.
        for i in range(len(out) - 1, -1, -1):
            prev = out[i]
            if not isinstance(prev, dict) or prev.get("role") != "assistant":
                break
            prev_tokens = _tokenize(prev.get("content") or "")
            if _jaccard(prev_tokens, new_tokens) >= DEDUP_OVERLAP_THRESHOLD:
                out[i] = m
                replaced = True
                break
        if not replaced:
            out.append(m)
    return out


def is_repetitive(
    response: str,
    history: list[dict],
    threshold: float = REPETITIVE_THRESHOLD,
) -> bool:
    """True when ``response`` is too similar to any of the last N assistant turns.

    Pure word-token Jaccard, no LLM, no embeddings. Walks
    ``history`` backwards and stops after ``REPETITIVE_LOOKBACK``
    assistant messages have been checked. Empty inputs → False.
    """
    if not response or not history:
        return False
    resp_tokens = _tokenize(response)
    if not resp_tokens:
        return False
    seen = 0
    for m in reversed(history):
        if not isinstance(m, dict) or m.get("role") != "assistant":
            continue
        if seen >= REPETITIVE_LOOKBACK:
            break
        seen += 1
        hist_tokens = _tokenize(m.get("content") or "")
        if not hist_tokens:
            continue
        if _jaccard(resp_tokens, hist_tokens) >= threshold:
            return True
    return False
