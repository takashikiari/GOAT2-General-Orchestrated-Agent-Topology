"""tests.test_tools_config — config/tools.toml loaders for goat_skills tools.

2026-07-09: moved read_file/write_file/shell/get_recent_logs/plugin-scan
constants out of hardcoded module literals into config/tools.toml, mirroring
the existing tools/web_config.py ([web]) pattern.
"""
from __future__ import annotations

from plugins.plugins_config import PLUGIN_SCAN_INTERVAL_SECONDS
from tools.get_recent_logs_config import GET_RECENT_LOGS_MAX_LINES
from tools.read_file_config import (
    READ_FILE_DEFAULT_MAX_CHARS,
    READ_FILE_HARD_BYTE_CAP,
    READ_FILE_MAX_MAX_CHARS,
    READ_FILE_MIN_MAX_CHARS,
    READ_FILE_PATH_PREVIEW_CHARS,
)
from tools.shell_config import (
    SHELL_CMD_LOG_CHARS,
    SHELL_DEFAULT_TIMEOUT,
    SHELL_MAX_OUTPUT_CHARS,
    SHELL_MAX_TIMEOUT,
    SHELL_MIN_TIMEOUT,
)
from tools.write_file_config import WRITE_FILE_MAX_CONTENT_CHARS, WRITE_FILE_PATH_PREVIEW_CHARS


def test_read_file_defaults():
    assert READ_FILE_DEFAULT_MAX_CHARS == 8000
    assert READ_FILE_MIN_MAX_CHARS == 100
    assert READ_FILE_MAX_MAX_CHARS == 100_000
    assert READ_FILE_HARD_BYTE_CAP == 2_000_000
    assert READ_FILE_PATH_PREVIEW_CHARS == 120


def test_write_file_defaults():
    assert WRITE_FILE_MAX_CONTENT_CHARS == 200_000
    assert WRITE_FILE_PATH_PREVIEW_CHARS == 120


def test_shell_defaults():
    assert SHELL_MAX_OUTPUT_CHARS == 4000
    assert SHELL_MIN_TIMEOUT == 1
    assert SHELL_MAX_TIMEOUT == 300
    assert SHELL_DEFAULT_TIMEOUT == 30
    assert SHELL_CMD_LOG_CHARS == 120


def test_get_recent_logs_default():
    assert GET_RECENT_LOGS_MAX_LINES == 500


def test_plugin_scan_interval_default():
    assert PLUGIN_SCAN_INTERVAL_SECONDS == 30


def test_read_file_config_falls_back_to_defaults_when_toml_missing(tmp_path, monkeypatch):
    import tools.read_file_config as mod

    monkeypatch.setattr(mod, "_CONFIG_PATH", tmp_path / "nonexistent.toml")
    cfg = mod._load()
    assert cfg == mod._DEFAULTS


def test_shell_config_load_reads_toml_override(tmp_path, monkeypatch):
    """_load() reflects a real TOML override -- proves the reader itself works.
    (The module-level SHELL_* constants are frozen at import time, same as
    every other config module in this codebase; only _load()'s live dict
    is practical to assert against without subprocess-level reimport.)"""
    import tools.shell_config as mod

    toml_path = tmp_path / "tools.toml"
    toml_path.write_text("[shell]\nmax_output_chars = 9999\n")
    monkeypatch.setattr(mod, "_CONFIG_PATH", toml_path)
    cfg = mod._load()
    assert cfg["shell"]["max_output_chars"] == 9999
