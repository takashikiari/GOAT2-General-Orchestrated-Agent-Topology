"""tests.test_admin_panel_tunnel — /api/tunnel route: the current Cloudflare tunnel link, behind admin auth."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

import admin_panel.telegram_auth as auth_module
from admin_panel import tunnel_state
from admin_panel.server import create_app

_BOT_TOKEN = "123456:test-bot-token"


def _headers() -> dict:
    fields = {"user": json.dumps({"id": 42, "first_name": "T"}), "auth_date": str(int(time.time()))}
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", _BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return {"X-Telegram-Init-Data": urlencode(fields)}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("config.settings.TELEGRAM_BOT_TOKEN", _BOT_TOKEN)
    monkeypatch.setattr(auth_module, "load_admin_chat_id", lambda: "42")
    tunnel_state.set_state("off")
    with TestClient(create_app(SimpleNamespace())) as c:
        yield c
    tunnel_state.set_state("off")


def test_requires_auth(client):
    assert client.get("/api/tunnel").status_code == 401


def test_returns_the_current_state(client):
    body = client.get("/api/tunnel", headers=_headers()).json()
    assert body["status"] == "off" and body["url"] is None
    tunnel_state.set_state("ready", url="https://new-words.trycloudflare.com")
    body = client.get("/api/tunnel", headers=_headers()).json()
    assert body["status"] == "ready" and body["url"] == "https://new-words.trycloudflare.com"
    assert set(body) == {"status", "url", "since", "detail"}


def test_a_restart_replaces_the_link(client):
    tunnel_state.set_state("ready", url="https://old-words.trycloudflare.com")
    tunnel_state.set_state("starting")
    tunnel_state.set_state("ready", url="https://fresh-words.trycloudflare.com")
    assert client.get("/api/tunnel", headers=_headers()).json()["url"] == "https://fresh-words.trycloudflare.com"
