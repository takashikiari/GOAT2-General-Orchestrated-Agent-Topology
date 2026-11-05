"""tests.test_tool_loop_config — orchestrator.tool_loop_config constants load correctly.

2026-08-14: moved out of memory.config / memory.config_extra — this governs
the orchestrator's tool-calling loop (round count, output-size cap, evidence
preview formatting), not a memory-tier setting, and now reads config/tools.toml
[tool_loop] instead of config/memory.toml.
"""
from __future__ import annotations

from orchestrator.tool_loop_config import (
    _DEFAULTS,
    TOOL_ARGS_PREVIEW_CHARS,
    TOOL_RESULT_HEAD_CHARS,
    TOOL_RESULT_SHORT_THRESHOLD,
    TOOL_RESULT_TAIL_CHARS,
)


def test_module_defaults_unchanged():
    """The hardcoded fallback defaults (used when tools.toml has no [tool_loop]
    or is missing entirely), not whatever config/tools.toml currently overrides
    them to -- max_iterations in particular is a live, intentionally-tuned
    override (see config/tools.toml's comment), not something to pin here."""
    assert _DEFAULTS["tool_loop"]["max_iterations"] == 6
    assert _DEFAULTS["tool_loop"]["max_output_chars"] == 60000
    assert _DEFAULTS["tool_loop"]["result_short_threshold"] == 400
    assert _DEFAULTS["tool_loop"]["result_head_chars"] == 200
    assert _DEFAULTS["tool_loop"]["result_tail_chars"] == 150
    assert _DEFAULTS["tool_loop"]["args_preview_chars"] == 200


def test_preview_formatting_constants_match_defaults():
    """These four aren't currently overridden in config/tools.toml, so the
    live-loaded constants should equal the defaults."""
    assert TOOL_RESULT_SHORT_THRESHOLD == 400
    assert TOOL_RESULT_HEAD_CHARS == 200
    assert TOOL_RESULT_TAIL_CHARS == 150
    assert TOOL_ARGS_PREVIEW_CHARS == 200


def test_load_reads_toml_override(tmp_path, monkeypatch):
    import orchestrator.tool_loop_config as mod

    toml_path = tmp_path / "tools.toml"
    toml_path.write_text("[tool_loop]\nmax_iterations = 42\n")
    monkeypatch.setattr(mod, "_CONFIG_PATH", toml_path)
    cfg = mod._load()
    assert cfg["tool_loop"]["max_iterations"] == 42


def test_missing_config_file_falls_back_to_defaults(tmp_path, monkeypatch):
    import orchestrator.tool_loop_config as mod

    monkeypatch.setattr(mod, "_CONFIG_PATH", tmp_path / "does_not_exist.toml")
    cfg = mod._load()
    assert cfg == mod._DEFAULTS
