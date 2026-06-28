"""tools.web_config — web tool config. Reads config/tools.toml ([web] section)."""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Final

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "tools.toml"

_DEFAULTS: dict = {
    "web": {
        "max_chars_default": 8000,
        "timeout_seconds": 30,
        "screenshot_dir": "/tmp/goat_screenshots",
    }
}


def _load() -> dict:
    try:
        with open(_CONFIG_PATH, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return _DEFAULTS


_cfg = _load().get("web", _DEFAULTS["web"])
_web = _DEFAULTS["web"]

WEB_MAX_CHARS: Final[int] = int(_cfg.get("max_chars_default", _web["max_chars_default"]))
WEB_TIMEOUT: Final[int] = int(_cfg.get("timeout_seconds", _web["timeout_seconds"]))
WEB_SCREENSHOT_DIR: Final[str] = str(_cfg.get("screenshot_dir", _web["screenshot_dir"]))

__all__ = ["WEB_MAX_CHARS", "WEB_TIMEOUT", "WEB_SCREENSHOT_DIR"]
