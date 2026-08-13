"""admin_panel.telegram_auth — Telegram Mini App initData verification.

Implements Telegram's official WebApp initData validation algorithm:
HMAC-SHA256 over the sorted, hash-excluded field set, keyed by
HMAC-SHA256("WebAppData", bot_token) — plus a freshness check on
auth_date. verify_init_data is a pure function; the FastAPI dependency
built on top of it is added in a later task.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from fastapi import Header, HTTPException

from admin_panel.auth_config import AUTH_MAX_AGE_SECONDS
from config import settings
from config.admin_chat import load_admin_chat_id

__all__ = ["verify_init_data", "require_admin_auth"]


def verify_init_data(init_data: str, bot_token: str, max_age_seconds: int) -> dict | None:
    """Validate a Telegram WebApp initData string; return its parsed fields or None.

    Returns None on any failure (bad signature, expired, malformed) —
    never raises on untrusted input.
    """
    try:
        pairs = dict(parse_qsl(init_data, strict_parsing=True))
    except (ValueError, TypeError):
        return None
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(computed_hash, received_hash):
        return None
    auth_date_raw = pairs.get("auth_date")
    if auth_date_raw is None:
        return None
    try:
        auth_date = int(auth_date_raw)
    except ValueError:
        return None
    if time.time() - auth_date > max_age_seconds:
        return None
    result = dict(pairs)
    if "user" in result:
        try:
            result["user"] = json.loads(result["user"])
        except json.JSONDecodeError:
            return None
    return result


async def require_admin_auth(
    x_telegram_init_data: str | None = Header(default=None),
) -> dict:
    """FastAPI dependency: 401s unless x_telegram_init_data is valid AND belongs to admin_chat_id.

    Deliberately different from telegram_interface.bot's _is_admin: an
    unconfigured admin_chat_id here means DENY everyone, not allow everyone
    — this route is potentially internet-reachable via the tunnel, unlike
    the bot's own chat-based check.
    """
    if not x_telegram_init_data:
        raise HTTPException(status_code=401, detail="missing X-Telegram-Init-Data header")
    parsed = verify_init_data(x_telegram_init_data, settings.TELEGRAM_BOT_TOKEN, AUTH_MAX_AGE_SECONDS)
    if parsed is None:
        raise HTTPException(status_code=401, detail="invalid or expired initData")
    user = parsed.get("user")
    admin_chat_id = load_admin_chat_id()
    if not admin_chat_id or not isinstance(user, dict) or str(user.get("id")) != admin_chat_id:
        raise HTTPException(status_code=401, detail="unauthorized user")
    return parsed
