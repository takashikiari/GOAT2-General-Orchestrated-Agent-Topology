"""tests.test_telegram_auth_verify — admin_panel.telegram_auth.verify_init_data.

Constructs signed initData strings by hand, using the same HMAC-SHA256
algorithm Telegram's real clients use, to prove verify_init_data accepts
exactly what a real Telegram Mini App would send and rejects tampering.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from admin_panel.telegram_auth import verify_init_data

_BOT_TOKEN = "123456:test-bot-token"


def _sign(fields: dict, bot_token: str) -> str:
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    return hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()


def _build_init_data(user_id: int = 42, auth_date: int | None = None, bot_token: str = _BOT_TOKEN) -> str:
    fields = {
        "query_id": "AAEXample",
        "user": json.dumps({"id": user_id, "first_name": "Test"}),
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
    }
    fields["hash"] = _sign(fields, bot_token)
    return urlencode(fields)


def test_valid_init_data_returns_parsed_user():
    result = verify_init_data(_build_init_data(user_id=42), _BOT_TOKEN, max_age_seconds=86400)
    assert result is not None
    assert result["user"]["id"] == 42


def test_tampered_payload_rejected():
    fields = {
        "query_id": "AAEXample",
        "user": json.dumps({"id": 42, "first_name": "Test"}),
        "auth_date": str(int(time.time())),
    }
    correct_hash = _sign(fields, _BOT_TOKEN)
    tampered = dict(fields)
    tampered["user"] = json.dumps({"id": 99, "first_name": "Test"})  # changed after signing
    tampered["hash"] = correct_hash  # hash from the ORIGINAL payload
    assert verify_init_data(urlencode(tampered), _BOT_TOKEN, max_age_seconds=86400) is None


def test_wrong_bot_token_rejected():
    init_data = _build_init_data(user_id=42, bot_token=_BOT_TOKEN)
    assert verify_init_data(init_data, "a-different-token", max_age_seconds=86400) is None


def test_expired_auth_date_rejected():
    old_auth_date = int(time.time()) - 100_000
    init_data = _build_init_data(user_id=42, auth_date=old_auth_date)
    assert verify_init_data(init_data, _BOT_TOKEN, max_age_seconds=86400) is None


def test_missing_hash_rejected():
    fields = {"auth_date": str(int(time.time())), "user": json.dumps({"id": 42})}
    assert verify_init_data(urlencode(fields), _BOT_TOKEN, max_age_seconds=86400) is None


def test_malformed_query_string_rejected():
    assert verify_init_data("noequalsatall", _BOT_TOKEN, max_age_seconds=86400) is None


def test_non_string_init_data_int_returns_none():
    """Non-string init_data (int) should return None, not raise TypeError."""
    assert verify_init_data(123, _BOT_TOKEN, max_age_seconds=86400) is None


def test_non_string_init_data_list_returns_none():
    """Non-string init_data (list) should return None, not raise TypeError."""
    assert verify_init_data(["a=1"], _BOT_TOKEN, max_age_seconds=86400) is None
