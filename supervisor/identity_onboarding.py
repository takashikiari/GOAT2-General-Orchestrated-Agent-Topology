"""Onboarding constants and helpers for GOAT identity.

Extracted from identity.py to keep that module within the 260-line budget.
Provides the welcome message, adaptive hints, and the two builder helpers
used by direct_response() on first-session turns.
"""
from __future__ import annotations

from typing import Final

__all__ = [
    "_ONBOARDING_KEY",
    "_build_welcome_message",
    "_build_adaptive_hint",
]

_ONBOARDING_KEY: Final[str] = "onboarding_done"

_WELCOME_MESSAGE: Final[str] = (
    "\n\n"
    "┌───────────────────────────────────────────────────────┐\n"
    "│  🐐 GOAT — always ready                          │\n"
    "│                                                     │\n"
    "│  I can read files, search the web, write code,       │\n"
    "│  check memory, analyze, compare, implement.          │\n"
    "│                                                     │\n"
    "│  Just tell me what you need.                         │\n"
    "└───────────────────────────────────────────────────────┘"
)

# Adaptive hints for the first 3 turns (rotating)
_HINTS: Final[list[str]] = [
    "\n\n🐐 I can read any file in the workspace — just tell me which one.",
    "\n\n🐐 I search the web in real time — give me a query.",
    "\n\n🐐 I can write code, analyze, compare — tell me what you need.",
]


def _build_welcome_message(turn: int, onboarding_done: bool) -> str:
    """Return the welcome message on the first turn of a new session.

    Args:
        turn: Current turn number (1-based).
        onboarding_done: Whether the welcome was already shown.

    Returns:
        Welcome string or empty string.
    """
    if onboarding_done:
        return ""
    if turn == 1:
        return _WELCOME_MESSAGE
    return ""


def _build_adaptive_hint(turn: int, onboarding_done: bool) -> str:
    """Return a rotating hint for turns 2–4 of the first session.

    Args:
        turn: Current turn number (1-based).
        onboarding_done: Whether onboarding is complete.

    Returns:
        Hint string or empty string.
    """
    if onboarding_done:
        return ""
    if 2 <= turn <= 4:
        idx = turn - 2  # turn 2 → hint 0, turn 3 → hint 1, turn 4 → hint 2
        if idx < len(_HINTS):
            return _HINTS[idx]
    return ""
