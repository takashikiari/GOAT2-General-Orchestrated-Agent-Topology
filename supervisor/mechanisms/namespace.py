"""Namespace classification — partition keys by source prefix.

Pure Python, no LLM, no I/O, no config. The taxonomy is the
GOAT-2.0 contract documented in CHANGELOG and identity:

  turn:*  → CONV   (conversation turns + user signals)
  dag:*   → DAG    (DAG coordination / results)
  goat:*  → GOAT   (GOAT routing + state)
  other   → SYS    (system / live data / unknown)

USAGE:
    from supervisor.mechanisms.namespace import classify_namespace

    label = classify_namespace("turn:abc123")   # → "CONV"
    label = classify_namespace("dag:result:1")  # → "DAG"

Labels are uppercase, stable strings. The mechanisms
(``freshness`` / ``staleness`` / ``context_builder``) compose
with this taxonomy to render the final ``[FRESH][CONV]`` block
that GOAT's system prompt references.
"""
from __future__ import annotations

from typing import Final

__all__ = ["NamespaceLabel", "classify_namespace", "is_dag_key", "is_conversational_key"]

# Frozen label set. String-typed constants so they can flow
# directly into system-prompt text without re-rendering.
CONV:    Final[str] = "CONV"
DAG:     Final[str] = "DAG"
GOAT:    Final[str] = "GOAT"
SYS:     Final[str] = "SYS"
NamespaceLabel = Final[str]  # type alias for callers

# Source prefixes that determine the label.
_PREFIX_TURN:  Final[str] = "turn:"
_PREFIX_DAG:   Final[str] = "dag:"
_PREFIX_GOAT:  Final[str] = "goat:"


def classify_namespace(key: object) -> str:
    """Map a working-memory key to its namespace label.

    Args:
        key: The raw key string. Non-strings (None, dict, etc.) map
            to ``"SYS"`` (safest default — unknown is treated as
            system / external).

    Returns:
        One of ``"CONV"``, ``"DAG"``, ``"GOAT"``, ``"SYS"``.
    """
    if not isinstance(key, str):
        return SYS
    if key.startswith(_PREFIX_DAG):
        return DAG
    if key.startswith(_PREFIX_TURN):
        return CONV
    if key.startswith(_PREFIX_GOAT):
        return GOAT
    return SYS


def is_dag_key(key: object) -> bool:
    """True when ``key`` is a DAG-namespaced working-memory key."""
    return isinstance(key, str) and key.startswith(_PREFIX_DAG)


def is_conversational_key(key: object) -> bool:
    """True when ``key`` is a conversational / state prefix (turn/goat)."""
    if not isinstance(key, str):
        return False
    return key.startswith(_PREFIX_TURN) or key.startswith(_PREFIX_GOAT)
