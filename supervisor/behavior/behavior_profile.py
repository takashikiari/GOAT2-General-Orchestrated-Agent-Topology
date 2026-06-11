"""BehaviorProfile TypedDict and pure serialization helpers for style persistence."""
from __future__ import annotations

import logging
from typing import Final, TypedDict

log = logging.getLogger("goat2.supervisor.behavior")

__all__ = ["BehaviorProfile", "serialize", "deserialize", "empty_profile"]

_FIELDS: Final[tuple[str, ...]] = (
    "formality", "tone", "vocabulary", "language", "humor", "length", "notes",
)


class BehaviorProfile(TypedDict, total=False):
    """Learned communication style — all fields optional, updated incrementally."""
    formality:  str  # casual | neutral | formal
    tone:       str  # friendly | technical | dry | direct
    vocabulary: str  # simple | technical | mixed
    language:   str  # e.g. "Romanian", "English", "mixed RO/EN"
    humor:      str  # none | dry | playful
    length:     str  # terse | moderate | verbose
    notes:      str  # ≤15-word free-form style observation


def serialize(profile: BehaviorProfile) -> str:
    """Convert profile to 'key: value' text block suitable for Letta block storage."""
    return "\n".join(
        f"{k}: {profile[k]}"  # type: ignore[literal-required]
        for k in _FIELDS
        if k in profile and profile[k]  # type: ignore[literal-required]
    )


def deserialize(text: str) -> BehaviorProfile:
    """Parse a 'key: value' text block back to BehaviorProfile; unknown keys are ignored."""
    profile: BehaviorProfile = {}
    for line in text.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            k = k.strip()
            if k in _FIELDS and v.strip():
                profile[k] = v.strip()  # type: ignore[literal-required]
    return profile


def empty_profile() -> BehaviorProfile:
    """Return an empty BehaviorProfile."""
    return {}
