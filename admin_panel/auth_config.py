"""admin_panel.auth_config — Telegram initData auth config. Reads config/admin_panel.toml ([auth] section)."""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Final

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "admin_panel.toml"

_DEFAULTS: dict = {
    "auth": {
        "max_age_seconds": 86400,
        "write_max_age_seconds": 14400,
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


def _write_max_age(cfg: dict) -> int:
    """initData age allowed on routes that act (chat): stricter than reads, never above the read max age."""
    read = int(cfg.get("max_age_seconds", _DEFAULTS["auth"]["max_age_seconds"]))
    return min(int(cfg.get("write_max_age_seconds", _DEFAULTS["auth"]["write_max_age_seconds"])), read)


AUTH_WRITE_MAX_AGE_SECONDS: Final[int] = _write_max_age(_cfg)

__all__ = ["AUTH_MAX_AGE_SECONDS", "AUTH_WRITE_MAX_AGE_SECONDS"]
