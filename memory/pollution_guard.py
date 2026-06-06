"""Validates facts before writing to Letta core — blocks inferred facts, detects contradictions."""
from __future__ import annotations

import logging
from typing import Final, Literal, TypedDict

__all__ = ["GuardDecision", "GuardResult", "PollutionGuard", "validate_fact"]

log = logging.getLogger("goat2.memory.guard")

GuardDecision = Literal["allowed", "blocked", "conflict"]


class GuardResult(TypedDict):
    """Immutable outcome of one pollution-guard check. No dict[str, Any]."""
    decision: GuardDecision
    reason:   str


_BLOCKED: Final[frozenset[str]] = frozenset({
    "agent_id", "passage_id", "search_key", "limit", "offset",
    "score", "source", "memory_type", "ttl", "count",
    "timestamp", "created_at", "updated_at",
})


def validate_fact(key: str, value: str, kind: str, existing_block: str) -> GuardResult:
    """Check one fact for quality before writing to Letta core. Pure — PyO3 candidate.

    Returns 'blocked' for inferred facts or technical keys, 'conflict' when the key
    exists with a different value, and 'allowed' otherwise.
    """
    nk = key.strip().lower()
    if kind != "explicit":
        return GuardResult(decision="blocked", reason="only explicit facts stored in Letta core")
    if nk in _BLOCKED or nk.endswith("_id"):
        return GuardResult(decision="blocked", reason=f"blocked technical key: {nk}")
    for line in existing_block.splitlines():
        if ":" not in line:
            continue
        ek = line.partition(":")[0].strip().lower()
        ev = line.partition(":")[2].strip()
        if ek == nk and ev != value.strip():
            return GuardResult(
                decision="conflict",
                reason=f"existing='{ev}' conflicts with new='{value.strip()}'",
            )
    return GuardResult(decision="allowed", reason="")


class PollutionGuard:
    """Stateless validator with logging — wraps pure validate_fact with observability."""

    def validate(self, key: str, value: str, kind: str, existing_block: str) -> GuardResult:
        """Validate one fact; logs conflicts at WARNING and blocked at DEBUG."""
        result = validate_fact(key, value, kind, existing_block)
        if result["decision"] == "conflict":
            log.warning("memory conflict — key=%r skipped. %s", key, result["reason"])
        elif result["decision"] == "blocked":
            log.debug("fact blocked — key=%r kind=%s: %s", key, kind, result["reason"])
        return result
