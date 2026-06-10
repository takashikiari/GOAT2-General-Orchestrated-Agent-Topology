"""Infer user communication style from recent turns and merge with the existing profile.

REGISTRY INJECTION (PHASE 4):
=============================
analyze_style() now requires `registry` parameter.
Uses registry.settings.agents.get() for model access.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final

from supervisor.behavior.behavior_profile import BehaviorProfile, deserialize, serialize
from supervisor.llm_utils import _call_llm, _extract_json

if TYPE_CHECKING:
    from config.registry import Registry

__all__ = ["analyze_style"]

log = logging.getLogger("goat2.supervisor.behavior")

_MIN_TURNS: Final[int] = 2   # minimum user turns before analysis is attempted
_MAX_TURNS: Final[int] = 20  # most-recent turns sent to the model
_SYSTEM: Final[str] = (
    "Analyze the user's messages and produce a concise communication style profile. "
    'Return JSON {"profile": {"formality": "casual|neutral|formal", '
    '"tone": "friendly|technical|dry|direct", '
    '"vocabulary": "simple|technical|mixed", '
    '"language": "<detected language, e.g. Romanian or mixed RO/EN>", '
    '"humor": "none|dry|playful", '
    '"length": "terse|moderate|verbose", '
    '"notes": "<≤15 words about distinctive patterns, e.g. skips punctuation>"}}. '
    "Include only fields you are confident about; omit uncertain ones. "
    "If an existing profile is provided, refine it with new evidence rather than replacing it."
)


async def analyze_style(
    user_turns: list[str],
    registry: "Registry",
    existing: str = "",
) -> str:
    """
    Call gpt-4o-mini to infer communication style from recent user messages.
    Merges new observations with the existing serialized profile.
    Returns serialized updated profile text, or existing unchanged on failure.
    Skips analysis when fewer than _MIN_TURNS turns are available.

    REGISTRY INJECTION (PHASE 4):
    =============================
    Requires registry parameter. Uses registry.settings.agents.get() for model access.
    """
    if len(user_turns) < _MIN_TURNS:
        log.debug("analyze_style: %d turn(s) < _MIN_TURNS=%d — skipping", len(user_turns), _MIN_TURNS)
        return existing
    existing_profile = deserialize(existing)
    msgs: list[dict] = [{"role": "system", "content": _SYSTEM}]
    if existing_profile:
        msgs.append({"role": "system", "content": f"Existing profile:\n{existing}"})
    msgs.append({"role": "user", "content": "\n---\n".join(user_turns[-_MAX_TURNS:])})
    try:
        raw  = await _call_llm(registry.settings.agents.get("memory"), msgs, temperature=0.0, json_mode=True)
        data = _extract_json(raw)
        new: dict = data.get("profile", {}) if isinstance(data, dict) else {}
        if not new:
            log.warning("analyze_style: LLM returned empty profile dict — raw: %.200s", raw)
            return existing
        merged: BehaviorProfile = {**existing_profile, **new}
        result = serialize(merged)
        log.debug("analyze_style: produced profile with %d field(s)", len(merged))
        return result
    except Exception as exc:
        log.warning("analyze_style: failed — %s", exc)
        return existing
