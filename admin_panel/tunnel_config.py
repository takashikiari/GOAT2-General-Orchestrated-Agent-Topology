"""admin_panel.tunnel_config — Cloudflare tunnel config. Reads config/admin_panel.toml ([tunnel] section)."""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Final

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "admin_panel.toml"

_DEFAULTS: dict = {
    "tunnel": {
        "enabled": False,
        "timeout_seconds": 15,
    }
}


def _load() -> dict:
    try:
        with open(_CONFIG_PATH, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return _DEFAULTS


_cfg = _load().get("tunnel", _DEFAULTS["tunnel"])

TUNNEL_ENABLED: Final[bool] = bool(_cfg.get("enabled", _DEFAULTS["tunnel"]["enabled"]))
TUNNEL_TIMEOUT_SECONDS: Final[int] = int(_cfg.get("timeout_seconds", _DEFAULTS["tunnel"]["timeout_seconds"]))

__all__ = ["TUNNEL_ENABLED", "TUNNEL_TIMEOUT_SECONDS"]
