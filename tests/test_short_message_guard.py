"""A message with fewer than min_words_cold content words never breaks the thread on its own (2026-11-07).

On 124 real turns, 13 of the 16 cold cuts at drift_cold=0.30 were short confirmations ("Da", "Ok",
"De ce?", "🤣🤣", "Goat"); with this guard 4 cuts remain, 1 of them wrong, and the same 3 real shifts are caught.
"""
from __future__ import annotations

import logging

from memory.activation import Activation, classify_turn
from memory.config import ACTIVATION_MIN_WORDS_COLD
from memory.turn_words import content_words

ORTHOGONAL = ([1.0, 0.0], [0.0, 1.0])  # centroid, query embedding: cosine 0 → below drift_cold


def _cls(query, recent=("parola de wifi",)):
    centroid, emb = ORTHOGONAL
    return classify_turn(query, Activation(centroid=centroid, recent_queries=list(recent)), emb)


def test_content_words_ignores_short_words_emoji_and_folds_diacritics():
    assert ACTIVATION_MIN_WORDS_COLD == 4
    samples = ("Da", "Ok", "🤣🤣", "De ce?", "Aprob", "Merge si restart pr 29", "Dar ți-am zis cum a murit lizi?")
    assert [content_words(t) for t in samples] == [0, 0, 0, 0, 1, 2, 5]
    assert content_words("Știi pisică") == content_words("Stii pisica") == 2


def test_short_confirmations_never_break_the_thread(caplog):
    caplog.set_level(logging.INFO, logger="memory.activation")
    for q in ("Da", "Ok", "De ce?", "🤣🤣", "Goat", "Aprob merge si restart"):
        assert _cls(q) == "drift", q
    assert any("short-message guard" in r.getMessage() for r in caplog.records)


def test_a_real_shift_with_enough_words_still_breaks():
    assert _cls("Dar ți-am zis cum a murit lizi?") == "cold"


def test_known_tradeoff_a_very_short_real_shift_is_kept_in_the_thread():
    """Deliberate: 2 content words is not enough evidence; drift still retrieves with the new query."""
    assert _cls("reteta de ciorba") == "drift"


def test_guard_only_touches_cold_decisions():
    same = Activation(centroid=[1.0, 0.0], recent_queries=["x"])
    assert classify_turn("Da", same, [1.0, 0.0]) == "warm"
    assert classify_turn("Da", None, [1.0, 0.0]) == "cold"  # no activation: still cold (nothing to keep)
