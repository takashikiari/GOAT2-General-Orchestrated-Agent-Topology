"""Style analyzer — score user communication style from recent
turns using pure-Python heuristics (no LLM).

Reads the heuristic thresholds from ``config/behavioral.toml``
via the modular loader. Returns a serialized profile text
suitable for ``store.save_style``.

USAGE:
    from supervisor.behavior.analyzer import analyze_style

    text = await analyze_style(user_turns, existing_profile_text)

SCORING: each BehaviorProfile field is scored by a small pure
function over the joined text of recent turns. The new profile
is merged over ``existing`` so the user's evolving style grows
incrementally. Returns ``existing`` unchanged when there are
fewer than ``min_turns_to_learn`` turns. Marker sets live in
``analyzer_markers`` to keep this file under 260 lines.
"""
from __future__ import annotations

import logging
from typing import Final

from supervisor.behavior.analyzer_markers import (
    CURT_WORDS, EMOJI_FRIENDLY, FRIENDLY_EMOJI, HUMOR_WORDS,
    LAUGH_TOKENS, POLITE_WORDS, SLANG_WORDS, SMILEY_EMOJI,
    TECH_WORDS,
)
from supervisor.behavior.profile import (
    BehaviorProfile, deserialize, empty_profile, serialize,
)

log = logging.getLogger("goat2.supervisor.behavior.analyzer")

__all__ = ["analyze_style", "load_thresholds"]


def load_thresholds() -> dict[str, float | int | str]:
    """Read [learning] and [style] from config/behavioral.toml.

    Returns a flat dict with the six knobs the analyzer reads.
    Missing section / missing key → safe default. The toml
    loader is non-fatal — a missing or unparseable file
    silently falls back to defaults so the analyzer stays
    usable in any environment.
    """
    out: dict[str, float | int | str] = {
        "min_turns_to_learn":    2,
        "max_turns_to_analyze":  20,
        "verbosity_default":     "moderate",
        "humor_threshold":       0.3,
        "formality_threshold":   0.5,
        "directness_threshold":  0.7,
    }
    try:
        from config.modular_loader import load_behavioral_config
        data = load_behavioral_config() or {}
        for k in ("min_turns_to_learn", "max_turns_to_analyze"):
            v = (data.get("learning", {}) or {}).get(k)
            if v is None:
                continue
            try:
                out[k] = int(v)
            except (TypeError, ValueError):
                log.debug("analyzer: %s=%r not int — using default", k, v)
        style = data.get("style", {}) or {}
        for k, caster in (("humor_threshold", float), ("formality_threshold", float),
                          ("directness_threshold", float), ("verbosity_default", str)):
            v = style.get(k)
            if v is None:
                continue
            try:
                out[k] = caster(v)
            except (TypeError, ValueError):
                log.debug("analyzer: style.%s=%r bad — using default", k, v)
    except Exception as exc:  # noqa: BLE001
        log.debug("analyzer: behavioral.toml load failed: %s", exc)
    return out
# Loaded once at import time; pure read of a static toml.
_THRESH: Final[dict[str, float | int | str]] = load_thresholds()

def _avg_length(turns: list[str]) -> float:
    """Mean character length of non-empty turns."""
    non_empty = [t for t in turns if t]
    if not non_empty:
        return 0.0
    return sum(len(t) for t in non_empty) / len(non_empty)


def _punctuation_density(text: str) -> float:
    """Punctuation chars per 100 characters of text."""
    if not text:
        return 0.0
    return sum(1 for c in text if c in ".,;:!?") * 100.0 / len(text)
def _cap_rate(text: str) -> float:
    """Fraction of alphabetic chars that are uppercase."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if c.isupper()) / len(letters)


def _score_formality(turns: list[str]) -> str:
    """``casual`` | ``neutral`` | ``formal`` — gated by style.formality_threshold.

    Politeness-vs-slang ratio above the threshold → formal,
    below → casual. Punctuation density + uppercase rate are
    tie-breakers.

    BUG-023 fix: an empty / whitespace-only input (no tokens)
    is 'neutral' — the fall-through must not return 'casual'
    when both polite and slang signals are zero. Without the
    token guard, a whitespace-only string had cap=0 and
    punct=0, which satisfied the ``cap<0.02 or punct<0.5``
    branch and returned 'casual'.
    """
    if not turns:
        return "neutral"
    text = " ".join(turns).lower().strip()
    if not text:
        return "neutral"  # whitespace-only input — no signal
    punct = sum(_punctuation_density(t) for t in turns) / len(turns)
    cap   = sum(_cap_rate(t) for t in turns) / len(turns)
    tokens = text.split()
    if not tokens:
        return "neutral"
    polite = sum(1 for w in tokens if w in POLITE_WORDS)
    slang  = sum(1 for w in tokens if w in SLANG_WORDS)
    threshold = float(_THRESH.get("formality_threshold", 0.5) or 0.5)
    polite_signal = polite + punct / 10
    slang_signal  = slang + max(0.0, 0.05 - cap) * 10
    total = polite_signal + slang_signal
    if total > 0:
        ratio = polite_signal / total
        if ratio >= threshold and polite >= 1:
            return "formal"
        if ratio <= (1 - threshold) and (slang >= 1 or cap < 0.02):
            return "casual"
    if polite >= 2 and punct >= 3:
        return "formal"
    if slang >= 2 or cap < 0.02 or punct < 0.5:
        return "casual"
    return "neutral"

def _score_tone(turns: list[str]) -> str:
    """``friendly`` | ``technical`` | ``dry`` | ``direct``.

    Curt-words density per turn above style.directness_threshold
    flags the reply as direct.

    BUG-024 fix: the previous ``has_emoji and tech <= 1`` shortcut
    returned 'friendly' whenever a single emoji appeared in any
    input — including long, technical messages that contained
    a celebratory emoji. The fix combines the emoji signal with
    the average turn length so a long technical message is not
    labelled 'friendly' just because it contains one emoji.
    """
    if not turns:
        return "dry"
    text = " ".join(turns).lower()
    joined = " ".join(turns)
    has_emoji = any(c in joined for c in EMOJI_FRIENDLY)
    tech = sum(1 for w in text.split() if w in TECH_WORDS)
    curt = sum(1 for t in turns for w in t.split() if w.lower() in CURT_WORDS)
    curt_per_turn = curt / max(1, len(turns))
    avg_len = _avg_length(turns)
    threshold = float(_THRESH.get("directness_threshold", 0.7) or 0.7)
    # BUG-024: a long technical message is 'friendly' only when
    # it also has a high ratio of friendly-emoji tokens to words.
    # A short message with one emoji is still 'friendly'.
    if has_emoji and tech <= 1 and avg_len < 80:
        return "friendly"
    if tech >= 2:
        return "technical"
    if curt_per_turn >= threshold:
        return "direct"
    return "dry"
def _score_vocabulary(turns: list[str]) -> str:
    """``simple`` | ``technical`` | ``mixed``."""
    if not turns:
        return "simple"
    tokens = [w for w in " ".join(turns).lower().split() if w]
    if not tokens:
        return "simple"
    rate = sum(1 for w in tokens if w in TECH_WORDS) / len(tokens)
    if rate >= 0.10:
        return "technical"
    if rate >= 0.03:
        return "mixed"
    return "simple"
def _score_language(turns: list[str]) -> str:
    """``Romanian`` | ``English`` | ``mixed RO/EN`` — delegates to lang_detect."""
    if not turns:
        return "English"
    try:
        from supervisor.classification.lang_detect import detect_language
        code = detect_language(" ".join(turns))
    except Exception:  # noqa: BLE001
        return "English"
    return {"ro": "Romanian", "en": "English", "mixed": "mixed RO/EN"}.get(code, "English")
def _score_humor(turns: list[str]) -> str:
    """``none`` | ``dry`` | ``playful`` — gated by style.humor_threshold."""
    if not turns:
        return "none"
    text = " ".join(turns).lower()
    joined = " ".join(turns)
    rate = sum(1 for tok in HUMOR_WORDS if tok in text) / max(1, len(turns))
    threshold = float(_THRESH.get("humor_threshold", 0.3) or 0.3)
    if rate >= threshold or any(tok in text for tok in ("😂", "🤣", "lol", "lmao", "haha")):
        return "playful"
    if any(c in joined for c in SMILEY_EMOJI):
        return "dry"
    return "none"
def _score_length(avg_len: float) -> str:
    """``terse`` | ``moderate`` | ``verbose``."""
    if avg_len < 25:
        return "terse"
    if avg_len < 120:
        return "moderate"
    return "verbose"
def _score_notes(turns: list[str], avg_len: float) -> str:
    """Short free-form observation (≤120 chars)."""
    if not turns:
        return "standard prose"
    text = " ".join(turns)
    notes: list[str] = []
    if _punctuation_density(text) < 0.5:
        notes.append("skips punctuation")
    if _cap_rate(text) < 0.03:
        notes.append("mostly lowercase")
    if avg_len < 25:
        notes.append("short replies")
    if any(c in text for c in FRIENDLY_EMOJI):
        notes.append("uses emoji")
    if any(tok in text.lower() for tok in LAUGH_TOKENS):
        notes.append("often laughs")
    if not notes:
        notes.append("standard prose")
    return ", ".join(notes)[:120]


__all__ = [
    "load_thresholds",
    "_avg_length",
    "_score_formality",
    "_score_tone",
    "_score_vocabulary",
    "_score_language",
    "_score_humor",
    "_score_length",
    "_score_notes",
]