"""tools.shell_config — shell_run tool config. Reads config/tools.toml ([shell] section)."""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Final

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "tools.toml"

_DEFAULTS: dict = {
    "shell": {
        "max_output_chars": 4000,
        "min_timeout": 1,
        "max_timeout": 300,
        "default_timeout": 30,
        "cmd_log_chars": 120,
    }
}


def _load() -> dict:
    try:
        with open(_CONFIG_PATH, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return _DEFAULTS


_cfg = _load().get("shell", _DEFAULTS["shell"])
_sh = _DEFAULTS["shell"]

SHELL_MAX_OUTPUT_CHARS: Final[int] = int(_cfg.get("max_output_chars", _sh["max_output_chars"]))
SHELL_MIN_TIMEOUT: Final[int] = int(_cfg.get("min_timeout", _sh["min_timeout"]))
SHELL_MAX_TIMEOUT: Final[int] = int(_cfg.get("max_timeout", _sh["max_timeout"]))
SHELL_DEFAULT_TIMEOUT: Final[int] = int(_cfg.get("default_timeout", _sh["default_timeout"]))
SHELL_CMD_LOG_CHARS: Final[int] = int(_cfg.get("cmd_log_chars", _sh["cmd_log_chars"]))

__all__ = [
    "SHELL_MAX_OUTPUT_CHARS", "SHELL_MIN_TIMEOUT", "SHELL_MAX_TIMEOUT",
    "SHELL_DEFAULT_TIMEOUT", "SHELL_CMD_LOG_CHARS",
]
