"""tools.get_recent_logs_config — get_recent_logs tool config.

Reads config/tools.toml ([get_recent_logs] section).
"""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Final

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "tools.toml"

_DEFAULTS: dict = {
    "get_recent_logs": {
        "max_lines": 500,
    }
}


def _load() -> dict:
    try:
        with open(_CONFIG_PATH, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return _DEFAULTS


_cfg = _load().get("get_recent_logs", _DEFAULTS["get_recent_logs"])

GET_RECENT_LOGS_MAX_LINES: Final[int] = int(
    _cfg.get("max_lines", _DEFAULTS["get_recent_logs"]["max_lines"])
)

__all__ = ["GET_RECENT_LOGS_MAX_LINES"]
