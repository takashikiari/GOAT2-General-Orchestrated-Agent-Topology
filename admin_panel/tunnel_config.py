"""admin_panel.tunnel_config — Cloudflare tunnel config. Reads config/admin_panel.toml ([tunnel] section).

Whether the tunnel opens is a per-machine choice, so it can also come from the machine's own .env:
ADMIN_PANEL_TUNNEL=true|false overrides [tunnel] enabled. The committed config/admin_panel.toml keeps the safe default
(off: the panel stays loopback-only), and a machine that wants the panel reachable from Telegram says so in its .env,
which is not in git, instead of carrying a local edit of the committed file (2026-11-11).
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Final, Mapping

from config import settings as _settings  # noqa: F401 - loads .env into os.environ (GOAT starts with `source .env`)

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "admin_panel.toml"
ENV_SWITCH = "ADMIN_PANEL_TUNNEL"

_DEFAULTS: dict = {
    "tunnel": {
        "enabled": False,
        "timeout_seconds": 15,
    }
}

_TRUE, _FALSE = ("1", "true", "yes", "on"), ("0", "false", "no", "off")


def _load() -> dict:
    try:
        with open(_CONFIG_PATH, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return _DEFAULTS


def resolve_enabled(cfg: dict, environ: Mapping[str, str]) -> bool:
    """ADMIN_PANEL_TUNNEL in the environment (.env) when it is a clear yes/no, else [tunnel] enabled, else off."""
    raw = environ.get(ENV_SWITCH, "").strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    return bool(cfg.get("enabled", _DEFAULTS["tunnel"]["enabled"]))


_cfg = _load().get("tunnel", _DEFAULTS["tunnel"])

TUNNEL_ENABLED: Final[bool] = resolve_enabled(_cfg, os.environ)
TUNNEL_TIMEOUT_SECONDS: Final[int] = int(_cfg.get("timeout_seconds", _DEFAULTS["tunnel"]["timeout_seconds"]))

__all__ = ["TUNNEL_ENABLED", "TUNNEL_TIMEOUT_SECONDS", "ENV_SWITCH", "resolve_enabled"]
