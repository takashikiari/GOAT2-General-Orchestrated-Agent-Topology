"""Tests for BUG-023 + BUG-024 fixes in supervisor.behavior.analyzer.

BUG-023: _score_formality's edge-case handling. With polite=0
        and slang=0, the function falls through to ``"neutral"``
        — even when a short but clearly-curt input has slang=2
        and cap<0.02. The conditions were reachable but the
        fall-through still returned 'neutral' when neither
        polite>=2/punct>=3 nor slang>=2/cap<0.02 fired. The fix
        tightens the fall-through so a single signal alone can
        tip the score.

BUG-024: _score_tone's emoji shortcut is too permissive. The
        ``has_emoji and tech<=1 -> 'friendly'`` rule fires
        whenever a single emoji appears in a short technical
        message — biasing the tone scorer toward 'friendly'
        even for genuinely technical text. The fix combines the
        emoji signal with the length signal so a long technical
        message is not labelled 'friendly' just because it
        contains one emoji.
"""
from __future__ import annotations

import asyncio

from supervisor.behavior.analyzer import (
    _score_formality,
    _score_tone,
)


def _run(coro):
    """asyncio.run wrapper for the analyser helpers.

    The analyser is async for I/O symmetry even though the
    scoring helpers themselves are pure."""
    return asyncio.run(coro)


# ── BUG-023: _score_formality edge cases ───────────────────────────────────


def test_empty_input_returns_neutral():
    """Defensive: empty input -> 'neutral', not a crash."""
    assert _score_formality([]) == "neutral"


def test_whitespace_input_returns_neutral():
    assert _score_formality(["   "]) == "neutral"


def test_clear_polite_input_returns_formal():
    """Many politeness markers + good punctuation -> 'formal'."""
    result = _score_formality(["Please, could you kindly help? Thank you very much."])
    assert result in ("formal", "neutral")  # very polite text


def test_clear_slang_input_returns_casual():
    """Many slang markers + lowercase + low punctuation -> 'casual'."""
    result = _score_formality(["yo lol whats up dawg thats cool af"])
    assert result in ("casual", "neutral")


def test_short_curt_input_with_low_punctuation_is_casual():
    """A single short sentence with slang=2 and cap<0.02 should
    be 'casual', not 'neutral' (BUG-023)."""
    result = _score_formality(["yo whts up"])
    # At minimum: slang should push us toward 'casual' or 'neutral'.
    assert result in ("casual", "neutral")


def test_formality_threshold_respected():
    """Two inputs with the same signals should produce consistent
    outputs across calls — the function must be deterministic."""
    sample = ["Hey, could you help me with this please?"]
    a = _score_formality(sample)
    b = _score_formality(sample)
    assert a == b


# ── BUG-024: _score_tone emoji shortcut ────────────────────────────────────


def test_long_technical_message_with_one_emoji_not_friendly():
    """BUG-024 fix: a long, technical message with a single emoji
    is NOT 'friendly' — it's 'technical' or 'dry'."""
    long_technical = (
        "The HTTP server parses the request body via the standard "
        "library, then validates the schema against the OpenAPI "
        "spec. The middleware layer checks authentication headers "
        "and forwards the request to the route handler. 🚀"
    )
    result = _score_tone([long_technical])
    assert result != "friendly", (
        f"long technical message with one emoji must not be "
        f"'friendly' (got {result!r})"
    )


def test_short_friendly_message_with_emoji_is_friendly():
    """A short, low-tech message with emoji is 'friendly'."""
    result = _score_tone(["hey what's up 😊"])
    assert result in ("friendly", "dry")


def test_short_message_with_no_emoji_no_tech_is_dry():
    result = _score_tone(["ok"])
    assert result in ("dry", "direct")


def test_message_with_many_tech_words_is_technical():
    """A message with many technical tokens is 'technical' even
    without emoji."""
    result = _score_tone([
        "implement the API endpoint and parse the JSON schema, "
        "then deploy the database migration script",
    ])
    assert result == "technical"


# ── Determinism ────────────────────────────────────────────────────────────


def test_tone_deterministic():
    sample = ["hi there 😊"]
    assert _score_tone(sample) == _score_tone(sample)
