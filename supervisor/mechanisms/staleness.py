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

Keywords are matched as case-insensitive substrings. This is
pure string membership — no regex, no language model.
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


def is_stale(entry: dict, intent: str, now: float) -> bool:
    """Return True when ``entry`` should be rendered with STALE_PREFIX.

    Args:
        entry: Working-memory record with optional ``stale`` flag
            and ``created_at_ts`` (seconds since epoch).
        intent: The raw user intent for this turn.
        now: Reference time in seconds since epoch.

    Returns:
        True when the entry is older than ``dag_max_age_seconds``
        AND the intent contains no DAG-related keyword. An entry
        that already carries ``stale=True`` is always stale.
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
    intent_lc = (intent or "").lower()
    return not any(kw in intent_lc for kw in DAG_INTENT_KEYWORDS)