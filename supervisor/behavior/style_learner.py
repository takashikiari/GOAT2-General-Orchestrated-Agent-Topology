"""Style learning — high-level entry point that composes the scorers.

The individual scorers (``_score_formality``, ``_score_tone``,
``_score_vocabulary``, ``_score_language``, ``_score_humor``,
``_score_length``, ``_make_notes``) live in
``supervisor.behavior.analyzer`` and stay under the 260-line
ceiling as a single file. This module owns the public
``analyze_style`` entry point: it loads the per-scorer samples,
invokes each scorer, and merges the new profile over the
existing one.

USAGE:
    from supervisor.behavior.style_learner import analyze_style
    new_text = await analyze_style(user_turns, existing)
"""
from __future__ import annotations

import logging
from typing import Final

from supervisor.behavior.analyzer import (
    _avg_length,
    _score_formality,
    _score_humor,
    _score_language,
    _score_length,
    _score_notes,
    _score_tone,
    _score_vocabulary,
    load_thresholds,
)
from supervisor.behavior.profile import (
    BehaviorProfile,
    deserialize,
    empty_profile,
    serialize,
)

log = logging.getLogger("goat2.supervisor.behavior.style_learner")

__all__ = ["analyze_style"]

# Loaded once at import time; pure read of a static toml file.
_THRESH: Final[dict[str, float | int | str]] = load_thresholds()


async def analyze_style(
    user_turns: list[str],
    existing: str = "",
) -> str:
    """Score user communication style from ``user_turns``.

    Returns ``existing`` unchanged when there are fewer than
    ``min_turns_to_learn`` turns. Otherwise merges the new
    scores over the existing profile so the user's evolving
    style grows incrementally.

    Args:
        user_turns: List of raw user messages (newest last).
        existing: Serialised profile text from a previous run.
            Empty string on the first call.

    Returns:
        Serialised profile text (the merged result). Same format
        as the input ``existing`` so it can be fed back next turn.
    """
    min_turns = int(_THRESH.get("min_turns_to_learn", 2) or 2)
    max_turns = int(_THRESH.get("max_turns_to_analyze", 20) or 20)
    if len(user_turns) < min_turns:
        log.debug(
            "analyze_style: %d turn(s) < min=%d — skipping",
            len(user_turns), min_turns,
        )
        return existing
    sample = list(user_turns[-max_turns:])
    existing_profile = deserialize(existing) if existing else empty_profile()
    avg_len = _avg_length(sample)
    new = BehaviorProfile(
        formality=_score_formality(sample),
        tone=_score_tone(sample),
        vocabulary=_score_vocabulary(sample),
        language=_score_language(sample),
        humor=_score_humor(sample),
        length=_score_length(avg_len),
        notes=_score_notes(sample, avg_len),
    )
    # Merge: new fields override existing; keep existing when
    # the new scorer returned nothing.
    merged = BehaviorProfile(
        formality  = new.formality  or existing_profile.formality,
        tone       = new.tone       or existing_profile.tone,
        vocabulary = new.vocabulary or existing_profile.vocabulary,
        language   = new.language   or existing_profile.language,
        humor      = new.humor      or existing_profile.humor,
        length     = new.length     or existing_profile.length,
        notes      = new.notes      or existing_profile.notes,
    )
    return serialize(merged)