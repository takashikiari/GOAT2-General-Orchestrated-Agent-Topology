"""Behavior analyzer — toml loading helpers.

Pure functions that read ``[learning]`` and ``[style]`` sections
from ``config/behavioral.toml`` and return typed defaults. Extracted
from ``behavior_analyzer.py`` to keep that file under the 260-line
ceiling. The analyzer imports these once at startup; the values
become module constants that the score functions reference.
"""
from __future__ import annotations

from typing import Any

from config.fallbacks import (
    CORRECTION_SENSITIVITY,
    DIRECTNESS_THRESHOLD,
    FORMALITY_THRESHOLD,
    HUMOR_THRESHOLD,
    LEARN_MAX_TURNS,
    LEARN_MIN_TURNS,
    VERBOSITY_DEFAULT,
)
from config.modular_loader import load_behavioral_config

__all__ = ["BEHAVIORAL_DEFAULTS"]


_behavioral_cfg = load_behavioral_config()
_learning_cfg = _behavioral_cfg.get("learning", {})
_style_cfg = _behavioral_cfg.get("style", {})


def _cfg_int(section: dict, key: str, default: int) -> int:
    raw = section.get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _cfg_float(section: dict, key: str, default: float) -> float:
    raw = section.get(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


BEHAVIORAL_DEFAULTS: dict[str, Any] = {
    "min_turns_to_learn":     _cfg_int(_learning_cfg, "min_turns_to_learn", LEARN_MIN_TURNS),
    "max_turns_to_analyze":   _cfg_int(_learning_cfg, "max_turns_to_analyze", LEARN_MAX_TURNS),
    "correction_sensitivity":  _cfg_float(_learning_cfg, "correction_sensitivity", CORRECTION_SENSITIVITY),
    "humor_threshold":         _cfg_float(_style_cfg, "humor_threshold", HUMOR_THRESHOLD),
    "formality_threshold":     _cfg_float(_style_cfg, "formality_threshold", FORMALITY_THRESHOLD),
    "directness_threshold":    _cfg_float(_style_cfg, "directness_threshold", DIRECTNESS_THRESHOLD),
    "verbosity_default":       _style_cfg.get("verbosity_default", VERBOSITY_DEFAULT) or VERBOSITY_DEFAULT,
}
