"""memory.config_extra — memory tier constants read from config/memory.toml.

Split from memory.config to stay under the file line-count convention. Reads
the SAME config/memory.toml file, exposing sections memory.config doesn't:
tool_loop preview/formatting constants, auto_promote, context_assembler,
observability thresholds, and entity_boost.
"""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Final

from memory.config_defaults import _DEFAULTS

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "memory.toml"


def _load() -> dict:
    try:
        with open(_CONFIG_PATH, "rb") as f:
            cfg = tomllib.load(f)
    except FileNotFoundError:
        return _DEFAULTS
    from memory.config_validator import validate_config
    validate_config(cfg)
    return cfg


_cfg = _load()

_tool_loop = _cfg.get("tool_loop", _DEFAULTS["tool_loop"])
TOOL_RESULT_SHORT_THRESHOLD: Final[int] = int(
    _tool_loop.get("result_short_threshold", _DEFAULTS["tool_loop"]["result_short_threshold"])
)
TOOL_RESULT_HEAD_CHARS: Final[int] = int(
    _tool_loop.get("result_head_chars", _DEFAULTS["tool_loop"]["result_head_chars"])
)
TOOL_RESULT_TAIL_CHARS: Final[int] = int(
    _tool_loop.get("result_tail_chars", _DEFAULTS["tool_loop"]["result_tail_chars"])
)
TOOL_ARGS_PREVIEW_CHARS: Final[int] = int(
    _tool_loop.get("args_preview_chars", _DEFAULTS["tool_loop"]["args_preview_chars"])
)

_auto_promote = _cfg.get("auto_promote", _DEFAULTS["auto_promote"])
AUTO_PROMOTE_CHUNK_SIZE: Final[int] = int(
    _auto_promote.get("chunk_size", _DEFAULTS["auto_promote"]["chunk_size"])
)
AUTO_PROMOTE_MIN_SURPLUS: Final[int] = int(
    _auto_promote.get("min_surplus", _DEFAULTS["auto_promote"]["min_surplus"])
)

_context_assembler_cfg = _cfg.get("context_assembler", _DEFAULTS["context_assembler"])
BLENDED_MIN_SCORE: Final[float] = float(
    _context_assembler_cfg.get("blended_min_score", _DEFAULTS["context_assembler"]["blended_min_score"])
)
SESSION_GAP_SECONDS: Final[int] = int(
    _context_assembler_cfg.get("session_gap_seconds", _DEFAULTS["context_assembler"]["session_gap_seconds"])
)

_observability_cfg = _cfg.get("observability", _DEFAULTS["observability"])
# greeting_confidence/recall_confidence deliberately stay hardcoded in
# memory/observability_collector.py, not config-driven: they only label the
# analytics tier (no real intent classifier exists) and never gate behaviour
# (the prefetch confidence gate was removed) — see that module's own comment.
OBS_MAX_MESSAGE_CHARS: Final[int] = int(
    _observability_cfg.get("max_message_chars", _DEFAULTS["observability"]["max_message_chars"])
)

_entity_boost_cfg = _cfg.get("entity_boost", _DEFAULTS["entity_boost"])
ENTITY_BOOST_WEIGHT: Final[float] = float(
    _entity_boost_cfg.get("weight", _DEFAULTS["entity_boost"]["weight"])
)

# aits.complexity_ref_length deliberately NOT read here: memory/aits.py's own
# docstring documents it as linguistic content (like _BASE_IDENTITY and the
# connector word list), not a tunable knob, and leaves it a hardcoded module
# constant on purpose.

__all__ = [
    "TOOL_RESULT_SHORT_THRESHOLD",
    "TOOL_RESULT_HEAD_CHARS",
    "TOOL_RESULT_TAIL_CHARS",
    "TOOL_ARGS_PREVIEW_CHARS",
    "AUTO_PROMOTE_CHUNK_SIZE",
    "AUTO_PROMOTE_MIN_SURPLUS",
    "BLENDED_MIN_SCORE",
    "SESSION_GAP_SECONDS",
    "OBS_MAX_MESSAGE_CHARS",
    "ENTITY_BOOST_WEIGHT",
]
