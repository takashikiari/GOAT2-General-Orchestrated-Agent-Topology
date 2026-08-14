"""config.admin_chat — shared admin_chat_id loader (goat2.toml).

Extracted from telegram_interface.bot so admin_panel modules can read the
same value: admin_panel.telegram_auth and admin_panel.tunnel cannot import
from telegram_interface.bot without creating a cycle
(bot.py -> _plugin_scanner.py -> admin_panel.* -> bot.py).
"""
from __future__ import annotations

import tomllib
from pathlib import Path

_ROOT = Path(__file__).parent.parent

__all__ = ["load_admin_chat_id"]


def load_admin_chat_id() -> str:
    """Read admin_chat_id from goat2.toml, or empty string if not configured."""
    cfg = _ROOT / "goat2.toml"
    if not cfg.exists():
        return ""
    try:
        with open(cfg, "rb") as f:
            data = tomllib.load(f)
        return str(data.get("interface", {}).get("telegram", {}).get("admin_chat_id", ""))
    except Exception:
        return ""
