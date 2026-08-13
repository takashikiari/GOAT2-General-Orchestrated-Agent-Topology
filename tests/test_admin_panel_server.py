"""tests.test_admin_panel_server — create_app wires all routers together, with
auth enforced on /api/* and the placeholder page open."""
from __future__ import annotations

import hashlib
import hmac
import json
import socket
import time
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

import admin_panel.admin_config as admin_config
import admin_panel.server as server_module
from admin_panel.server import create_app, start

_BOT_TOKEN = "123456:test-bot-token"
_ADMIN_ID = "777"


def _sign(fields: dict, bot_token: str) -> str:
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    return hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()


def _auth_headers() -> dict:
    fields = {
        "user": json.dumps({"id": int(_ADMIN_ID), "first_name": "Test"}),
        "auth_date": str(int(time.time())),
    }
    fields["hash"] = _sign(fields, _BOT_TOKEN)
    return {"X-Telegram-Init-Data": urlencode(fields)}


class _FakeAnalytics:
    def get_report(self):
        return {"total_requests": 0}


class _FakePermanent:
    async def get_all_facts(self):
        return {}


class _FakeWorking:
    async def get_messages(self, chat_id):
        return []

    async def list_chat_ids(self):
        return []


class _FakeEpisodic:
    async def get_recent(self, chat_id, limit=20):
        return []

    async def get_oldest(self, limit, chat_id=None):
        return []

    async def list_chat_ids(self):
        return []


class _FakeRegistry:
    def __init__(self):
        self.memory_analytics = _FakeAnalytics()
        self.permanent_memory = _FakePermanent()
        self.working_memory = _FakeWorking()
        self.episodic_memory = _FakeEpisodic()


def test_index_page_is_not_authenticated():
    client = TestClient(create_app(_FakeRegistry()))
    assert client.get("/").status_code == 200


def test_create_app_wires_all_routers_with_auth(monkeypatch):
    monkeypatch.setattr("config.settings.TELEGRAM_BOT_TOKEN", _BOT_TOKEN)
    monkeypatch.setattr("admin_panel.telegram_auth.load_admin_chat_id", lambda: _ADMIN_ID)
    client = TestClient(create_app(_FakeRegistry()))
    headers = _auth_headers()
    assert client.get("/api/metrics", headers=headers).status_code == 200
    assert client.get("/api/logs", headers=headers).status_code == 200
    assert client.get("/api/memory/facts", headers=headers).status_code == 200
    assert client.get("/api/memory/working/chat1", headers=headers).status_code == 200
    assert client.get("/api/memory/episodic/chat1", headers=headers).status_code == 200
    assert client.get("/api/conversations", headers=headers).status_code == 200
    assert client.get("/api/conversations/chat1", headers=headers).status_code == 200


def test_api_routes_reject_unauthenticated_requests():
    client = TestClient(create_app(_FakeRegistry()))
    assert client.get("/api/metrics").status_code == 401


@pytest.mark.asyncio
async def test_start_never_raises_when_create_app_fails(monkeypatch):
    def _boom(_registry):
        raise RuntimeError("router registration exploded")

    monkeypatch.setattr(server_module, "create_app", _boom)

    # Must not raise — start() is documented to never propagate exceptions,
    # even ones that occur before uvicorn.Server._serve() is reached.
    await start(_FakeRegistry())


@pytest.mark.asyncio
async def test_start_never_raises_when_port_already_bound(monkeypatch):
    # Regression test for the SystemExit gap: uvicorn's Server.startup()
    # doesn't raise OSError when the bind fails — it catches it internally
    # and calls sys.exit(1), which raises SystemExit (a BaseException, not
    # an Exception). A plain `except Exception` in start() would miss this
    # entirely and let SystemExit propagate up into python-telegram-bot's
    # own (KeyboardInterrupt, SystemExit) handler, silently killing the
    # whole bot. Bind a real socket first so the admin panel's own attempt
    # to bind the same host:port actually fails the way it would in prod.
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        blocker.bind((admin_config.ADMIN_HOST, 0))
        blocker.listen(1)
        bound_port = blocker.getsockname()[1]

        monkeypatch.setattr(server_module, "ADMIN_PORT", bound_port)

        # Must not raise — this is the exact scenario finding #1 fixes.
        await start(_FakeRegistry())
    finally:
        blocker.close()
