"""tests.test_telegram_auth_dependency — admin_panel.telegram_auth.require_admin_auth."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import admin_panel.telegram_auth as auth_module

_BOT_TOKEN = "123456:test-bot-token"
_ADMIN_ID = "42"


def _sign(fields: dict, bot_token: str) -> str:
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    return hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()


def _build_init_data(user_id: int, bot_token: str = _BOT_TOKEN) -> str:
    fields = {
        "user": json.dumps({"id": user_id, "first_name": "Test"}),
        "auth_date": str(int(time.time())),
    }
    fields["hash"] = _sign(fields, bot_token)
    return urlencode(fields)


def _client(monkeypatch):
    monkeypatch.setattr("config.settings.TELEGRAM_BOT_TOKEN", _BOT_TOKEN)
    monkeypatch.setattr(auth_module, "load_admin_chat_id", lambda: _ADMIN_ID)
    app = FastAPI()

    @app.get("/protected", dependencies=[Depends(auth_module.require_admin_auth)])
    async def protected():
        return {"ok": True}

    return TestClient(app)


def test_valid_admin_init_data_allowed(monkeypatch):
    client = _client(monkeypatch)
    resp = client.get("/protected", headers={"X-Telegram-Init-Data": _build_init_data(user_id=42)})
    assert resp.status_code == 200


def test_missing_header_rejected(monkeypatch):
    client = _client(monkeypatch)
    assert client.get("/protected").status_code == 401


def test_wrong_user_rejected(monkeypatch):
    client = _client(monkeypatch)
    resp = client.get("/protected", headers={"X-Telegram-Init-Data": _build_init_data(user_id=99)})
    assert resp.status_code == 401


def test_invalid_signature_rejected(monkeypatch):
    client = _client(monkeypatch)
    bad = _build_init_data(user_id=42, bot_token="a-different-token")
    resp = client.get("/protected", headers={"X-Telegram-Init-Data": bad})
    assert resp.status_code == 401


def test_no_admin_configured_rejects_everyone(monkeypatch):
    monkeypatch.setattr("config.settings.TELEGRAM_BOT_TOKEN", _BOT_TOKEN)
    monkeypatch.setattr(auth_module, "load_admin_chat_id", lambda: "")
    app = FastAPI()

    @app.get("/protected", dependencies=[Depends(auth_module.require_admin_auth)])
    async def protected():
        return {"ok": True}

    client = TestClient(app)
    resp = client.get("/protected", headers={"X-Telegram-Init-Data": _build_init_data(user_id=42)})
    assert resp.status_code == 401


def test_empty_bot_token_rejects_everyone(monkeypatch):
    # Finding 2: an empty TELEGRAM_BOT_TOKEN would make the HMAC secret key
    # a publicly-derivable constant (hmac.new(b"WebAppData", b"", sha256)),
    # letting anyone forge initData for any user id. Sign against that same
    # empty-string key here to prove the guard denies even an
    # otherwise-valid-looking (correctly self-signed) payload.
    monkeypatch.setattr("config.settings.TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(auth_module, "load_admin_chat_id", lambda: _ADMIN_ID)
    app = FastAPI()

    @app.get("/protected", dependencies=[Depends(auth_module.require_admin_auth)])
    async def protected():
        return {"ok": True}

    client = TestClient(app)
    forged = _build_init_data(user_id=int(_ADMIN_ID), bot_token="")
    resp = client.get("/protected", headers={"X-Telegram-Init-Data": forged})
    assert resp.status_code == 401
