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

__all__ = ["verify_init_data"]


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
