"""telegram_interface.coord_poller_config — config/coord_poller.toml loader (agent-coord v2 poller)."""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Final

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "coord_poller.toml"

_DEFAULTS: dict = {
    "enabled": True,
    "interval_seconds": 30,
}


def _load() -> dict:
    try:
        with open(_CONFIG_PATH, "rb") as f:
            return tomllib.load(f)
    except (FileNotFoundError, tomllib.TOMLDecodeError):
        return {}


_cfg = _load().get("coord_poller", {})

COORD_POLLER_ENABLED: Final[bool] = bool(_cfg.get("enabled", _DEFAULTS["enabled"]))
COORD_POLLER_INTERVAL_SECONDS: Final[int] = max(5, int(_cfg.get("interval_seconds", _DEFAULTS["interval_seconds"])))

__all__ = ["COORD_POLLER_ENABLED", "COORD_POLLER_INTERVAL_SECONDS"]
