"""Tests for BUG-017 fix: intent complexity scoring.

The previous ``classify_intent`` was a trivial mapper from
``turn.action`` to an enum. It told you the kind of turn the
LLM produced (direct/clarify/dag) but said nothing about how
complex the user's intent was.

The fix adds a separate ``score_intent_complexity`` function
that analyses the raw user intent and returns a ``ComplexityScore``
(dataclass with a numeric score and a label). The score can
later be used by the supervisor to choose how to handle the
turn — e.g. short low-complexity inputs may skip the memory
recall step, while long multi-clause inputs may warrant a DAG.

The scorer is pure Python (no LLM, no regex) — it uses token
count, clause-punctuation density, and a small keyword list of
"complex" verbs (analyse, compare, build, etc.) to compute a
numeric score in [0.0, 1.0].
"""
from __future__ import annotations

import pytest

from supervisor.classification.intent_complexity import (
    ComplexityScore,
    COMPLEXITY_THRESHOLDS,
    COMPLEX_VERBS,
    score_intent_complexity,
)


# ── Threshold constants are exported ────────────────────────────────────────


def test_thresholds_define_3_bands():
    assert isinstance(COMPLEXITY_THRESHOLDS, tuple)
    assert len(COMPLEXITY_THRESHOLDS) == 2  # trivial/simple/simple+ boundary
    for v in COMPLEXITY_THRESHOLDS:
        assert 0.0 <= v <= 1.0


def test_complex_verbs_is_non_empty_frozenset():
    assert isinstance(COMPLEX_VERBS, frozenset)
    assert len(COMPLEX_VERBS) > 0
    for verb in COMPLEX_VERBS:
        assert isinstance(verb, str)
        assert verb == verb.lower()


# ── Empty / trivial inputs ─────────────────────────────────────────────────


def test_empty_intent_is_trivial():
    score = score_intent_complexity("")
    assert score.value == 0.0
    assert score.label == "trivial"


def test_whitespace_only_is_trivial():
    score = score_intent_complexity("   \n\t  ")
    assert score.value == 0.0
    assert score.label == "trivial"


def test_single_word_is_trivial_or_simple():
    """Single words may be trivial (no clause markers, no complex verbs)
    or simple if they contain a complex verb (e.g. 'analysă' alone)."""
    score = score_intent_complexity("ok")
    assert score.label in ("trivial", "simple")


def test_short_intent_with_question_mark_is_simple():
    """A short question — 'where is X?' — is simple, not complex."""
    score = score_intent_complexity("unde e X?")
    assert score.label in ("trivial", "simple")
    assert score.value < COMPLEXITY_THRESHOLDS[1]


# ── Multi-clause / keyword-bearing inputs are complex ─────────────────────


def test_multi_clause_intent_is_complex():
    score = score_intent_complexity(
        "Analizează performanța, compară cu baseline-ul și construiește "
        "un raport cu grafice."
    )
    assert score.label == "complex"
    assert score.value >= COMPLEXITY_THRESHOLDS[1]


def test_intent_with_complex_verb_is_at_least_simple():
    """A single sentence with a complex verb ('analizează', 'compară')
    bumps the score above the trivial band."""
    score = score_intent_complexity("Analizează codul")
    assert score.label in ("simple", "complex")


def test_very_long_intent_is_complex():
    long_intent = (
        "Construiește o aplicație web care să permită utilizatorilor "
        "să se înregistreze, să autentifice, să vizualizeze un "
        "dashboard cu statistici în timp real, să primească "
        "notificări pe email, să exporte datele în CSV și PDF, și "
        "să partajeze rapoarte cu alți utilizatori." * 3
    )
    score = score_intent_complexity(long_intent)
    assert score.label == "complex"


# ── Score is bounded ──────────────────────────────────────────────────────


@pytest.mark.parametrize("text", [
    "",
    "ok",
    "what time is it?",
    "Analizează tot și construiește un plan detaliat pentru " + "x " * 100,
])
def test_score_in_unit_interval(text):
    score = score_intent_complexity(text)
    assert 0.0 <= score.value <= 1.0


# ── ComplexityScore dataclass ──────────────────────────────────────────────


def test_complexity_score_dataclass_fields():
    score = score_intent_complexity("hello")
    assert isinstance(score, ComplexityScore)
    assert isinstance(score.value, float)
    assert score.label in ("trivial", "simple", "complex")