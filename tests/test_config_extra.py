"""tests.test_config_extra — memory.config_extra constants load correctly.

2026-07-09: moved orchestrator.py's tool-summary preview constants,
auto_promote, context_assembler, and entity_boost from hardcoded module
literals into config/memory.toml, mirroring memory.config's own pattern.
"""
from __future__ import annotations

from memory.config_extra import (
    AUTO_PROMOTE_CHUNK_SIZE,
    AUTO_PROMOTE_MIN_SURPLUS,
    BLENDED_MIN_SCORE,
    ENTITY_BOOST_WEIGHT,
    OBS_MAX_MESSAGE_CHARS,
    SESSION_GAP_SECONDS,
    TOOL_ARGS_PREVIEW_CHARS,
    TOOL_RESULT_HEAD_CHARS,
    TOOL_RESULT_SHORT_THRESHOLD,
    TOOL_RESULT_TAIL_CHARS,
)


def test_tool_loop_preview_defaults():
    assert TOOL_RESULT_SHORT_THRESHOLD == 400
    assert TOOL_RESULT_HEAD_CHARS == 200
    assert TOOL_RESULT_TAIL_CHARS == 150
    assert TOOL_ARGS_PREVIEW_CHARS == 200


def test_auto_promote_defaults():
    assert AUTO_PROMOTE_CHUNK_SIZE == 50
    assert AUTO_PROMOTE_MIN_SURPLUS == 4


def test_blended_min_score_default():
    assert BLENDED_MIN_SCORE == 0.25


def test_session_gap_seconds_default():
    assert SESSION_GAP_SECONDS == 1800


def test_obs_max_message_chars_default():
    assert OBS_MAX_MESSAGE_CHARS == 200


def test_entity_boost_weight_default():
    assert ENTITY_BOOST_WEIGHT == 0.2


def test_load_reads_toml_override(tmp_path, monkeypatch):
    import memory.config_extra as mod

    toml_path = tmp_path / "memory.toml"
    toml_path.write_text("[entity_boost]\nweight = 0.99\n")
    monkeypatch.setattr(mod, "_CONFIG_PATH", toml_path)
    cfg = mod._load()
    assert cfg["entity_boost"]["weight"] == 0.99
