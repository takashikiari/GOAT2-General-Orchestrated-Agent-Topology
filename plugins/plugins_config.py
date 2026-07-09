"""plugins.plugins_config — plugin hot-reload config. Reads config/tools.toml ([plugins] section)."""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Final

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "tools.toml"

_DEFAULTS: dict = {
    "plugins": {
        "scan_interval_seconds": 30,
    }
}


def _load() -> dict:
    try:
        with open(_CONFIG_PATH, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return _DEFAULTS


_cfg = _load().get("plugins", _DEFAULTS["plugins"])

PLUGIN_SCAN_INTERVAL_SECONDS: Final[int] = int(
    _cfg.get("scan_interval_seconds", _DEFAULTS["plugins"]["scan_interval_seconds"])
)

__all__ = ["PLUGIN_SCAN_INTERVAL_SECONDS"]
