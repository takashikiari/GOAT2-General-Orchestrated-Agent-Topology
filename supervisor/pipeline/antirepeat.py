"""Pure-Python anti-repetition helpers for the GOAT turn pipeline.

Two cheap mechanisms, no LLM, no embeddings:

  - ``dedup_history(messages)`` collapses consecutive near-duplicate
    assistant messages *before* they enter the LLM prompt. Used by
    ``goat_call.goat_turn`` to remove the previous assistant turn from
    the prompt when it overlaps the most recent one ≥ 90 %. This breaks
    the "LLM sees its own previous output and copies it" feedback loop.

  - ``is_repetitive(response, history)`` tags a freshly-generated
    response as low-confidence when it overlaps ≥ 85 % of any of the
    last 3 assistant turns. Used after the LLM call to set
    ``GoatTurnResult.source = "repetitive"`` so downstream channels
    (Telegram/CLI) can deprioritize it.

Jaccard similarity over word tokens. O(message_length). Sub-millisecond
on typical turns. Both functions are pure — no I/O, no LLM, no
singletons — so they are safe to import from any hot path.
"""
from __future__ import annotations

import re
from typing import Final

__all__ = ["dedup_history", "is_repetitive"]

_DEDUP_OVERLAP_THRESHOLD: Final[float] = 0.90
_REPETITIVE_THRESHOLD: Final[float] = 0.85
_REPETITIVE_LOOKBACK: Final[int] = 3
_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"\w+", re.UNICODE)


def _tokenize(text: str) -> set[str]:
    """Word-token set for Jaccard. Lower-cased, regex-tokenized. O(len(text))."""
    return {tok.lower() for tok in _TOKEN_RE.findall(text or "")}


def _jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard similarity: |A ∩ B| / |A ∪ B|. 0.0 when both empty."""
    if not a and not b:
        return 0.0
    union = len(a | b)
    if not union:
        return 0.0
    return len(a & b) / union


def dedup_history(messages: list[dict]) -> list[dict]:
    """Collapse consecutive near-duplicate assistant messages. Pure-Python.

    Walks the message list once. For each assistant message, compares
    its word-token Jaccard overlap against the most recent kept
    assistant. If overlap ≥ ``_DEDUP_OVERLAP_THRESHOLD``, the new
    message *replaces* the kept one (we want the latest version, in
    case the LLM corrected itself). User/system messages pass through
    untouched. Returns a new list; never mutates the input.
    """
    out: list[dict] = []
    for m in messages or []:
        role = m.get("role")
        content = m.get("content") or ""
        if role != "assistant":
            out.append(m)
            continue
        new_tokens = _tokenize(content)
        replaced = False
        for i in range(len(out) - 1, -1, -1):
            prev = out[i]
            if prev.get("role") != "assistant":
                break  # only consider the consecutive run
            prev_tokens = _tokenize(prev.get("content") or "")
            if _jaccard(prev_tokens, new_tokens) >= _DEDUP_OVERLAP_THRESHOLD:
                out[i] = m  # keep the latest, drop the older
                replaced = True
                break
        if not replaced:
            out.append(m)
    return out


def is_repetitive(
    response: str,
    history: list[dict],
    threshold: float = _REPETITIVE_THRESHOLD,
) -> bool:
    """True when ``response`` is too similar to any of the last N assistant turns.

    Pure word-token Jaccard, no LLM, no embeddings. Walks ``history``
    backwards and stops after ``_REPETITIVE_LOOKBACK`` assistant
    messages have been checked. Returns False on empty inputs.
    """
    if not response or not history:
        return False
    resp_tokens = _tokenize(response)
    if not resp_tokens:
        return False
    seen = 0
    for m in reversed(history):
        if m.get("role") != "assistant":
            continue
        if seen >= _REPETITIVE_LOOKBACK:
            break
        seen += 1
        hist_tokens = _tokenize(m.get("content") or "")
        if not hist_tokens:
            continue
        if _jaccard(resp_tokens, hist_tokens) >= threshold:
            return True
    return False