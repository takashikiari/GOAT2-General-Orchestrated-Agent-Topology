"""
memory.budget — retrieval budget helpers: token estimation and hard size limits.

The primary per-intent budget is now dynamic (AITS, ``memory.aits``); the fixed
``MAX_CONTEXT_TOKENS`` is a fallback only. Two reusable concerns remain here:

    - ``enforce_result_limit`` caps how many items a single search returns.
    - ``enforce_context_budget`` caps the combined token size of a block list,
      dropping lowest-priority blocks (from the end) first — a generic utility
      (kept exported); ``MemoryLayers.assemble_context`` applies L2/L3 sizing
      directly via its own trim/fit helpers instead, because the coarse
      "drop whole block + warn" behaviour is wrong for partial L3 recall.

Both log a WARNING **only when truncation actually happens**. Defaults come from
``memory.config`` (config/memory.toml), so callers may omit the explicit limit.
"""
from __future__ import annotations

import logging

import tiktoken

log = logging.getLogger(__name__)

__all__ = ["estimate_tokens", "enforce_result_limit", "enforce_context_budget"]

# cl100k_base: no public tokenizer for the configured model (DeepSeek, an
# OpenAI-compatible API — see config/settings.py MODEL_NAME/BASE_URL) exists
# as a pip-installable package, so this is used as a universal approximation.
# Measured against 20 real production L3 entries (2026-07-12): the previous
# len(text)//4 heuristic undercounted by ~30-40% vs. this encoding (up to 2.33x
# on short Romanian-diacritic strings) — a strict accuracy improvement over the
# flat heuristic, not a claim of exact DeepSeek-token parity.
_ENCODING = tiktoken.get_encoding("cl100k_base")


def estimate_tokens(text: str) -> int:
    """Token-count estimate via tiktoken's cl100k_base encoding.

    Not billing-accurate for DeepSeek specifically (no public DeepSeek
    tokenizer package exists), but a real BPE tokenizer is a strict accuracy
    improvement over the old ``len(text) // 4`` flat heuristic, which
    systematically undercounted real (especially diacritic) text.
    """
    return len(_ENCODING.encode(text))


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