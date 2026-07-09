"""telegram_interface.telegram_config — bot config. Reads config/telegram.toml ([dedupe] section)."""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Final

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "telegram.toml"

_DEFAULTS: dict = {
    "dedupe": {
        "ttl_seconds": 86400,
    }
}


def _load() -> dict:
    try:
        with open(_CONFIG_PATH, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return _DEFAULTS


_cfg = _load().get("dedupe", _DEFAULTS["dedupe"])

UPDATE_DEDUPE_TTL_SECONDS: Final[int] = int(
    _cfg.get("ttl_seconds", _DEFAULTS["dedupe"]["ttl_seconds"])
)

__all__ = ["UPDATE_DEDUPE_TTL_SECONDS"]
