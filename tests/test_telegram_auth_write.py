"""tests.test_telegram_auth_write — write routes (chat) require fresher initData than read routes."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import admin_panel.auth_config as auth_config
import admin_panel.telegram_auth as auth_module

_BOT_TOKEN = "123456:test-bot-token"
_ADMIN_ID = "42"


def _init_data(age_seconds: int) -> str:
    fields = {"user": json.dumps({"id": 42, "first_name": "T"}), "auth_date": str(int(time.time()) - age_seconds)}
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", _BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def _client(monkeypatch):
    monkeypatch.setattr("config.settings.TELEGRAM_BOT_TOKEN", _BOT_TOKEN)
    monkeypatch.setattr(auth_module, "load_admin_chat_id", lambda: _ADMIN_ID)
    monkeypatch.setattr(auth_module, "AUTH_MAX_AGE_SECONDS", 86400)
    monkeypatch.setattr(auth_module, "AUTH_WRITE_MAX_AGE_SECONDS", 14400)
    app = FastAPI()

    @app.get("/read", dependencies=[Depends(auth_module.require_admin_auth)])
    async def read():
        return {"ok": True}

    @app.post("/write", dependencies=[Depends(auth_module.require_admin_write_auth)])
    async def write():
        return {"ok": True}

    return TestClient(app)


def _h(age):
    return {"X-Telegram-Init-Data": _init_data(age)}


def test_fresh_init_data_passes_both(monkeypatch):
    c = _client(monkeypatch)
    assert c.get("/read", headers=_h(60)).status_code == 200
    assert c.post("/write", headers=_h(60)).status_code == 200


def test_five_hour_old_init_data_reads_but_cannot_write(monkeypatch):
    c = _client(monkeypatch)
    assert c.get("/read", headers=_h(5 * 3600)).status_code == 200
    assert c.post("/write", headers=_h(5 * 3600)).status_code == 401


def test_write_still_needs_the_admin_user_and_a_header(monkeypatch):
    c = _client(monkeypatch)
    assert c.post("/write").status_code == 401
    monkeypatch.setattr(auth_module, "load_admin_chat_id", lambda: "999")
    assert c.post("/write", headers=_h(60)).status_code == 401


def test_write_max_age_default_and_never_above_the_read_max_age(tmp_path, monkeypatch):
    assert auth_config.AUTH_WRITE_MAX_AGE_SECONDS == 14400
    toml_path = tmp_path / "admin_panel.toml"
    toml_path.write_text("[auth]\nmax_age_seconds = 600\nwrite_max_age_seconds = 99999\n")
    monkeypatch.setattr(auth_config, "_CONFIG_PATH", toml_path)
    assert auth_config._write_max_age(auth_config._load().get("auth", {})) == 600
