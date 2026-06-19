"""DAG-result staleness check — flag an entry whose live content
no longer matches its recorded hash.

Pure Python, no LLM, no I/O. The mechanism itself is dependency-free:
``is_stale(entry, intent)`` is a pure decision function over the
entry's metadata + age.

USAGE:
    from supervisor.mechanisms.staleness import is_stale, STALE_PREFIX

    if is_stale(entry, intent, now):
        rendered = STALE_PREFIX + " " + entry["content"]

DECISION RULE:
    An entry is stale when EITHER:
      (a) ``entry["stale"]`` is set to True (explicit flag — DAG
          tools set this when a concurrent writer overwrote the key,
          or when the auto-clean task ran prematurely); OR
      (b) the entry's ``created_at_ts`` is older than
          ``dag_max_age_seconds`` AND the user's intent does NOT
          mention a DAG-related keyword (``dag``, ``task``,
          ``result``, ``workflow``, ``pipeline``).

KEYWORD MATCHING (BUG-012 fix):
    The original implementation used plain substring matching, so
    a user saying "tag the result" or "show me the taskbar" would
    keep an old DAG entry fresh. The new implementation matches
    whole tokens: a keyword matches only when surrounded by
    whitespace, string boundaries, or non-alphanumeric characters.
    Matching is case-insensitive but otherwise literal — no regex.
"""
from __future__ import annotations

from typing import Final

from supervisor.mechanisms.freshness import _CFG as _FRESHNESS_CFG

__all__ = ["STALE_PREFIX", "DAG_INTENT_KEYWORDS", "is_stale"]

# Stable prefix that callers prepend to flagged content so GOAT
# can pattern-match staleness cheaply in the system prompt.
STALE_PREFIX: Final[str] = "[STALE]"

# Substrings (lowercase) whose presence in the user's intent
# signals they are asking about DAG state / results; in that case
# an old DAG entry is NOT considered stale — the user wants it.
DAG_INTENT_KEYWORDS: Final[tuple[str, ...]] = (
    "dag", "task", "result", "workflow", "pipeline",
)


def _token_contains_keyword(token: str, keyword: str) -> bool:
    """True when ``keyword`` appears in ``token`` as a whole word.

    Handles the trailing-s case (``tasks`` contains ``task``) and
    trailing-suffix words (``pipelines`` contains ``pipeline``) by
    accepting keyword matches at the start of the token, with the
    next character being end-of-string OR a non-alphanumeric char.
    This is the cheapest correct implementation: O(n) substring
    scan, no regex.
    """
    idx = token.find(keyword)
    while idx >= 0:
        after = idx + len(keyword)
        end_of_token = after >= len(token)
        next_char = token[after] if not end_of_token else ""
        if end_of_token or not next_char.isalnum():
            return True
        idx = token.find(keyword, idx + 1)
    return False


def _intent_mentions_dag(intent: str) -> bool:
    """True when any DAG_INTENT_KEYWORD appears in ``intent`` as a
    whole token (or as a prefix followed by a non-alnum char).

    Args:
        intent: Raw user intent (any case). Whitespace-split into
            tokens; each token is checked independently.

    Returns:
        True if at least one keyword is found in a whole-token match.
    """
    if not intent:
        return False
    # Whitespace split is sufficient — punctuation stays attached to
    # tokens and is treated as a word boundary by the alnum check.
    for token in intent.lower().split():
        for kw in DAG_INTENT_KEYWORDS:
            if _token_contains_keyword(token, kw):
                return True
    return False


def is_stale(entry: dict, intent: str, now: float) -> bool:
    """Return True when ``entry`` should be rendered with STALE_PREFIX.

    Args:
        entry: Working-memory record with optional ``stale`` flag
            and ``created_at_ts`` (seconds since epoch).
        intent: The raw user intent for this turn.
        now: Reference time in seconds since epoch.

    Returns:
        True when the entry is older than ``dag_max_age_seconds``
        AND the intent contains no DAG-related keyword (matched as
        a whole token, not a substring). An entry that already
        carries ``stale=True`` is always stale.
    """
    if not isinstance(entry, dict):
        return False
    if entry.get("stale") is True:
        return True
    # Non-DAG entries don't have a "stale" semantics here.
    from supervisor.mechanisms.namespace import classify_namespace
    if classify_namespace(entry.get("key", "")) != "DAG":
        return False
    ts = entry.get("created_at_ts")
    try:
        age = now - float(ts)
    except (TypeError, ValueError):
        # Unparseable timestamp on a DAG entry → treat as stale.
        return True
    if age < _FRESHNESS_CFG["dag_max_age_seconds"]:
        return False
    # Old DAG entry: only fresh if the user is asking about DAGs.
    return not _intent_mentions_dag(intent)