"""Infer user communication style from recent turns — pure scoring, no LLM.

Replaces the LLM-based analyzer with a deterministic scoring
function over the same surface: ``BehaviorProfile`` (formality,
tone, vocabulary, language, humor, length, notes). Each field is
scored from a small set of signals; the new profile is merged
with the existing one so the user's evolving style is captured
incrementally rather than replaced wholesale.

External signature is unchanged (``analyze_style(user_turns,
registry, existing)``). The ``registry`` parameter is unused but
preserved so call sites in ``turn_persistence`` and
``behavior_session`` continue to work. Returns the serialized
updated profile text; returns ``existing`` unchanged when there
are fewer than ``_MIN_TURNS`` turns.

SCORING SIGNALS (pure):
  - formality: punctuation density, capitalization, politeness words
  - tone: emoji presence, technical terms, "ok/yes" curtness
  - vocabulary: technical-term density
  - language: delegates to ``lang_detect`` (already pure)
  - humor: laugh tokens, smiley/emoji
  - length: average message length bucketed terse/moderate/verbose
  - notes: short free-form observation of distinctive patterns

The analyzer still merges with ``existing`` so the profile grows
over time without losing previously-learned fields.
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Final

from supervisor.behavior.behavior_profile import (
    BehaviorProfile, deserialize, empty_profile, serialize,
)

if TYPE_CHECKING:
    from config.registry import Registry

__all__ = ["analyze_style"]

log = logging.getLogger("goat2.supervisor.behavior")

_MIN_TURNS: Final[int] = 2
_MAX_TURNS: Final[int] = 20

# Politeness markers — raise formality.
_POLITE: Final[frozenset[str]] = frozenset({
    "please", "thanks", "thank", "mulțumesc", "multumesc", "te", "rog",
    "vă", "va", "dumneavoastră", "dumneavoastra",
})

# Slang / very casual markers — lower formality.
_SLANG: Final[frozenset[str]] = frozenset({
    "yo", "sup", "wassup", "lol", "lmao", "rofl", "omg", "idk",
    "tbh", "ngl", "bruh", "bro", "dude", "y'all", "yall",
    "haide", "bă", "ba", "frate", "mă", "ma", "nasol", "fain",
})

# Technical terms — bias vocabulary toward "technical".
_TECH: Final[frozenset[str]] = frozenset({
    "api", "function", "class", "method", "error", "exception",
    "module", "package", "import", "compile", "debug", "deploy",
    "kubernetes", "docker", "regex", "thread", "async", "await",
    "request", "response", "endpoint", "json", "yaml", "toml",
    "schema", "migration", "transaction", "query", "index",
})

# Laughter / humor tokens.
_HUMOR: Final[frozenset[str]] = frozenset({
    "haha", "hehe", "hihi", "lol", "lmao", "rofl", "ahaha",
    "😂", "🤣", "😆", "😅", "😄", "😁", "😊", "🙂",
    ":)", ":-)", ";)", ";-)", ":d", "=d", "xd", "xd",
})

# Smiley / non-laugh emoji (used for tone=friendly / humor=dry).
_SMILEY: Final[frozenset[str]] = frozenset({
    "🙂", "🙃", "😉", "😌", "😀", "😃", "😊", "😇", "🤗", "👍",
    "👋", "🤝", "❤️", "💚", "💙", "💛", "🧡", "💜", "🖤", "🤍",
})


def _avg_length(turns: list[str]) -> float:
    """Mean character length of non-empty turns."""
    if not turns:
        return 0.0
    return sum(len(t) for t in turns if t) / max(1, sum(1 for t in turns if t))


def _punctuation_density(text: str) -> float:
    """Punctuation chars per 100 characters of text."""
    if not text:
        return 0.0
    punct = sum(1 for c in text if c in ".,;:!?")
    return punct * 100.0 / len(text)


def _capitalization_rate(text: str) -> float:
    """Fraction of alphabetic chars that are uppercase. >0.2 = lots of caps."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if c.isupper()) / len(letters)


def _score_formality(turns: list[str]) -> str:
    """casual | neutral | formal — based on punctuation, caps, politeness markers."""
    if not turns:
        return "neutral"
    text = " ".join(turns).lower()
    punct_density = sum(_punctuation_density(t) for t in turns) / len(turns)
    cap_rate = sum(_capitalization_rate(t) for t in turns) / len(turns)
    polite_hits = sum(1 for w in text.split() if w in _POLITE)
    slang_hits = sum(1 for w in text.split() if w in _SLANG)
    if polite_hits >= 2 and punct_density >= 3:
        return "formal"
    if slang_hits >= 2 or cap_rate < 0.02 or punct_density < 0.5:
        return "casual"
    return "neutral"


def _score_tone(turns: list[str]) -> str:
    """friendly | technical | dry | direct."""
    text = " ".join(turns).lower()
    has_emoji = any(c in "😀😁😂🤣😊🙂😉👍❤️" for c in " ".join(turns))
    tech_hits = sum(1 for w in text.split() if w in _TECH)
    curt_words = sum(1 for t in turns for w in t.split() if w.lower() in {"ok", "yes", "no", "da", "nu", "yep", "nope"})
    if has_emoji and tech_hits <= 1:
        return "friendly"
    if tech_hits >= 2:
        return "technical"
    if curt_words >= len(turns):
        return "direct"
    return "dry"


def _score_vocabulary(turns: list[str]) -> str:
    """simple | technical | mixed — based on technical-term density."""
    text = " ".join(turns).lower()
    tokens = [w for w in text.split() if w]
    if not tokens:
        return "simple"
    tech_hits = sum(1 for w in tokens if w in _TECH)
    rate = tech_hits / len(tokens)
    if rate >= 0.10:
        return "technical"
    if rate >= 0.03:
        return "mixed"
    return "simple"


def _score_language(turns: list[str]) -> str:
    """ro | en | mixed — delegates to lang_detect on the joined text."""
    from supervisor.classification.lang_detect import detect_language
    text = " ".join(turns)
    code = detect_language(text)
    return {"ro": "Romanian", "en": "English", "mixed": "mixed RO/EN"}.get(code, "English")


def _score_humor(turns: list[str]) -> str:
    """none | dry | playful."""
    text = " ".join(turns).lower()
    if any(tok in text for tok in _HUMOR):
        return "playful"
    if any(c in "".join(turns) for c in _SMILEY):
        return "dry"
    return "none"


def _score_length(avg_len: float) -> str:
    """terse | moderate | verbose — based on average character length."""
    if avg_len < 25:
        return "terse"
    if avg_len < 120:
        return "moderate"
    return "verbose"


def _make_notes(turns: list[str], avg_len: float) -> str:
    """Short free-form observation of distinctive patterns. ≤15 words."""
    notes: list[str] = []
    text = " ".join(turns)
    if _punctuation_density(text) < 0.5:
        notes.append("skips punctuation")
    if _capitalization_rate(text) < 0.03:
        notes.append("mostly lowercase")
    if avg_len < 25:
        notes.append("short replies")
    if any(c in text for c in _SMILEY):
        notes.append("uses emoji")
    if any(tok in text.lower() for tok in ("haha", "lol", "😂")):
        notes.append("often laughs")
    if not notes:
        notes.append("standard prose")
    return ", ".join(notes)[:120]


def analyze_style(
    user_turns: list[str],
    registry: "Registry | None" = None,  # unused — kept for backward compat
    existing: str = "",
) -> str:
    """Score user communication style from ``user_turns`` — pure, no LLM.

    Returns the serialized updated profile text. The new profile
    is merged with ``existing`` so the user's evolving style
    grows incrementally. Returns ``existing`` unchanged when
    there are fewer than ``_MIN_TURNS`` turns.

    The ``registry`` parameter is unused but preserved for
    backward compatibility with the call sites in
    ``turn_persistence`` and ``behavior_session``.
    """
    if len(user_turns) < _MIN_TURNS:
        log.debug("analyze_style: %d turn(s) < _MIN_TURNS=%d — skipping", len(user_turns), _MIN_TURNS)
        return existing
    sample = list(user_turns[-_MAX_TURNS:])
    existing_profile = deserialize(existing) if existing else empty_profile()
    avg_len = _avg_length(sample)
    new: BehaviorProfile = {
        "formality": _score_formality(sample),
        "tone": _score_tone(sample),
        "vocabulary": _score_vocabulary(sample),
        "language": _score_language(sample),
        "humor": _score_humor(sample),
        "length": _score_length(avg_len),
        "notes": _make_notes(sample, avg_len),
    }
    merged: BehaviorProfile = {**existing_profile, **new}
    log.debug(
        "analyze_style: %d turn(s), avg_len=%.0f, fields=%d",
        len(sample), avg_len, len(merged),
    )
    return serialize(merged)
