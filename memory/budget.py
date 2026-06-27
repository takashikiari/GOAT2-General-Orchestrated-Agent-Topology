"""
memory.budget — retrieval budget helpers: token estimation and hard size limits.

These enforce the Step-3 guarantee that no layer combination ever produces a
prompt larger than the configured budget. Two concerns are separated:

    - ``enforce_result_limit`` caps how many items a single search returns.
    - ``enforce_context_budget`` caps the combined token size of assembled
      context blocks, dropping lowest-priority blocks (from the end) first.

Both log a WARNING **only when truncation actually happens** — no log spam when
nothing is dropped. Defaults come from ``memory.config`` (config/memory.toml),
so callers may omit the explicit limit and still get the configured cap.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

__all__ = ["estimate_tokens", "enforce_result_limit", "enforce_context_budget"]


def estimate_tokens(text: str) -> int:
    """Rough token-count estimate: ``len(text) // 4``.

    A safe heuristic, not a billing-accurate counter. ``tiktoken`` is installed
    in this project but is intentionally not used here: its encodings match
    OpenAI tokenization, not DeepSeek's, so it would not be more accurate for
    the configured model. If a model-accurate tokenizer becomes required, swap
    this body for it.
    """
    return len(text) // 4


def enforce_result_limit(results: list[dict], max_results: int | None = None) -> list[dict]:
    """Truncate ``results`` to at most ``max_results`` entries (first N kept).

    Callers are expected to have already sorted by relevance/recency, so the
    first N are the most important. ``max_results`` defaults to
    ``MAX_RESULTS_PER_SEARCH`` from config when omitted. Logs a WARNING with
    the original and kept counts **only when entries are dropped**; returns the
    list unchanged (and silently) when it already fits.
    """
    if max_results is None:
        from memory.config import MAX_RESULTS_PER_SEARCH  # lazy — keeps budget.py standalone
        max_results = MAX_RESULTS_PER_SEARCH
    if len(results) <= max_results:
        return results
    kept = results[:max_results]
    log.warning(
        "enforce_result_limit: dropped %d/%d results (kept %d, cap=%d)",
        len(results) - max_results, len(results), len(kept), max_results,
    )
    return kept


def enforce_context_budget(
    text_blocks: list[str], max_tokens: int | None = None,
) -> list[str]:
    """Drop blocks from the END (lowest priority) until total fits ``max_tokens``.

    ``text_blocks`` MUST be in descending priority order — most important first,
    least important last — because blocks are removed from the end until the
    combined estimated token count is within budget. Returns the kept blocks in
    their original order. ``max_tokens`` defaults to ``MAX_CONTEXT_TOKENS`` from
    config when omitted. Logs a WARNING with the number dropped and the
    before/after token counts **only when truncation happens**; returns the
    list unchanged (and silently) when it already fits.
    """
    if max_tokens is None:
        from memory.config import MAX_CONTEXT_TOKENS  # lazy — keeps budget.py standalone
        max_tokens = MAX_CONTEXT_TOKENS
    before = estimate_tokens("\n".join(text_blocks))
    if before <= max_tokens:
        return list(text_blocks)
    kept = list(text_blocks)
    while kept and estimate_tokens("\n".join(kept)) > max_tokens:
        kept.pop()
    after = estimate_tokens("\n".join(kept)) if kept else 0
    log.warning(
        "enforce_context_budget: dropped %d/%d blocks (tokens %d -> %d, budget %d)",
        len(text_blocks) - len(kept), len(text_blocks), before, after, max_tokens,
    )
    return kept