"""tests.test_estimate_tokens — unit tests for memory.budget.estimate_tokens.

Bug 2: the old ``len(text)//4`` heuristic systematically undercounted real
Romanian text (diacritics especially) by ~30-40% vs. tiktoken's cl100k_base
encoding. estimate_tokens now delegates to tiktoken directly, so it should
match cl100k_base exactly for these sample strings (tiktoken is now the
ground truth, not an approximation to compare loosely against).
"""
from __future__ import annotations

import tiktoken

from memory.budget import estimate_tokens

_ENC = tiktoken.get_encoding("cl100k_base")

# Real-shaped Romanian diacritic strings (from the bug report + representative
# L3-style content).
_SAMPLES = [
    "Ce cauți mă?",
    "Bună, ce mai faci azi?",
    "Nu știu, poate mâine mergem.",
    "Îți place cafeaua sau ceaiul?",
    "Mulțumesc mult pentru ajutor!",
    "Sună-mă când ajungi acasă, te rog.",
]


def test_matches_tiktoken_cl100k_base_exactly():
    for s in _SAMPLES:
        assert estimate_tokens(s) == len(_ENC.encode(s)), s


def test_empty_string_is_zero_tokens():
    assert estimate_tokens("") == 0


def test_no_longer_undercounts_short_diacritic_string():
    """The bug-report example: old heuristic gave 3, real tokenizer gives 7."""
    assert estimate_tokens("Ce cauți mă?") == 7


def test_signature_returns_int():
    assert isinstance(estimate_tokens("hello world"), int)
