"""BehaviorProfile dataclass — typed view of a learned
communication style, plus pure serialization helpers.

Pure Python, no LLM, no I/O. The dataclass mirrors the seven
fields the analyzer (``behavior.analyzer``) scores:

    formality, tone, vocabulary, language, humor, length, notes

USAGE:
    from supervisor.behavior.profile import (
        BehaviorProfile, serialize, deserialize, empty_profile,
    )

    profile: BehaviorProfile = deserialize(raw_text)
    text:    str             = serialize(profile)
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

__all__ = ["BehaviorProfile", "serialize", "deserialize", "empty_profile"]

# Field names — frozen tuple so callers can iterate safely.
_FIELDS: tuple[str, ...] = (
    "formality",
    "tone",
    "vocabulary",
    "language",
    "humor",
    "length",
    "notes",
)


@dataclass
class BehaviorProfile:
    """Learned user communication style — all fields optional.

    Attributes:
        formality:  ``casual`` | ``neutral`` | ``formal``
        tone:       ``friendly`` | ``technical`` | ``dry`` | ``direct``
        vocabulary: ``simple`` | ``technical`` | ``mixed``
        language:   e.g. ``Romanian`` / ``English`` / ``mixed RO/EN``
        humor:      ``none`` | ``dry`` | ``playful``
        length:     ``terse`` | ``moderate`` | ``verbose``
        notes:      short free-form style observation (≤15 words)
    """

    formality:  str = ""
    tone:       str = ""
    vocabulary: str = ""
    language:   str = ""
    humor:      str = ""
    length:     str = ""
    notes:      str = ""


def empty_profile() -> BehaviorProfile:
    """Return a fresh BehaviorProfile with all fields empty."""
    return BehaviorProfile()


def serialize(profile: BehaviorProfile) -> str:
    """Convert profile to ``key: value`` text block for Letta storage.

    Empty / falsy fields are omitted. Output is stable across
    runs (fields are emitted in ``_FIELDS`` order).
    """
    lines: list[str] = []
    d = asdict(profile) if hasattr(profile, "__dataclass_fields__") else dict(profile)
    for k in _FIELDS:
        v = d.get(k, "")
        if v:
            lines.append(f"{k}: {v}")
    return "\n".join(lines)


def deserialize(text: str) -> BehaviorProfile:
    """Parse ``key: value`` text back into a BehaviorProfile.

    Unknown keys are silently ignored. Lines without a colon
    are ignored. Empty values are ignored.
    """
    profile = empty_profile()
    if not text:
        return profile
    for line in text.splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        k = k.strip()
        v = v.strip()
        if k in _FIELDS and v:
            setattr(profile, k, v)
    return profile


def to_dict(profile: BehaviorProfile) -> dict[str, Any]:
    """Return a plain dict view (drops empty fields)."""
    d = asdict(profile) if hasattr(profile, "__dataclass_fields__") else dict(profile)
    return {k: v for k, v in d.items() if v}