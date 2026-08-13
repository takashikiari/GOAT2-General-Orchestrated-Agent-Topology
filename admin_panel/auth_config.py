"""admin_panel.auth_config — Telegram initData auth config. Reads config/admin_panel.toml ([auth] section)."""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Final

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "admin_panel.toml"

_DEFAULTS: dict = {
    "auth": {
        "max_age_seconds": 86400,
    }
}


def _load() -> dict:
    try:
        with open(_CONFIG_PATH, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return _DEFAULTS


_cfg = _load().get("auth", _DEFAULTS["auth"])

AUTH_MAX_AGE_SECONDS: Final[int] = int(_cfg.get("max_age_seconds", _DEFAULTS["auth"]["max_age_seconds"]))

__all__ = ["AUTH_MAX_AGE_SECONDS"]
