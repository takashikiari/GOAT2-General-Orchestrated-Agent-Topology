"""admin_panel.admin_config — admin panel server config. Reads config/admin_panel.toml ([server] section)."""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Final

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "admin_panel.toml"

_DEFAULTS: dict = {
    "server": {
        "host": "127.0.0.1",
        "port": 8765,
    }
}


def _load() -> dict:
    try:
        with open(_CONFIG_PATH, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return _DEFAULTS


_cfg = _load().get("server", _DEFAULTS["server"])

ADMIN_HOST: Final[str] = str(_cfg.get("host", _DEFAULTS["server"]["host"]))
ADMIN_PORT: Final[int] = int(_cfg.get("port", _DEFAULTS["server"]["port"]))

__all__ = ["ADMIN_HOST", "ADMIN_PORT"]
