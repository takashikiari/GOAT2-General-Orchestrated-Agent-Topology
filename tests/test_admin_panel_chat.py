"""tests.test_admin_panel_chat — /api/chat routes over the real app (auth enforced) with a fake GOAT."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections import defaultdict
from types import SimpleNamespace
from urllib.parse import urlencode
import asyncio

import pytest
from fastapi.testclient import TestClient

import admin_panel.telegram_auth as auth_module
from admin_panel.server import create_app
from orchestrator import turn_events
from orchestrator.chat_service import ChatService

_BOT_TOKEN = "123456:test-bot-token"
_ADMIN_ID = "42"


def _headers(age: int = 0) -> dict:
    fields = {"user": json.dumps({"id": 42, "first_name": "T"}), "auth_date": str(int(time.time()) - age)}
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", _BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return {"X-Telegram-Init-Data": urlencode(fields)}


class _Gate:
    """Fake Orchestrator.run: one tool call, then waits until released."""

    def __init__(self):
        self.release = None

    async def __call__(self, text, chat_id, *, on_context_assembled=None):
        if self.release is None:
            self.release = asyncio.Event()
        turn_events.emit({"type": "tool_start", "id": "1", "name": "get_recent_logs", "args": "{}"})
        await self.release.wait()
        turn_events.emit({"type": "tool_end", "id": "1", "name": "get_recent_logs", "result": "lines"})
        return f"am primit: {text}"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("config.settings.TELEGRAM_BOT_TOKEN", _BOT_TOKEN)
    monkeypatch.setattr(auth_module, "load_admin_chat_id", lambda: _ADMIN_ID)
    monkeypatch.setattr(auth_module, "AUTH_WRITE_MAX_AGE_SECONDS", 14400)
    gate = _Gate()
    svc = ChatService(run=gate, locks=defaultdict(asyncio.Lock), chat_id=lambda: _ADMIN_ID, max_queue=2)
    app = create_app(SimpleNamespace(chat_service=svc))
    with TestClient(app) as c:
        c.gate, c.svc = gate, svc
        yield c


def _wait(client, turn_id, status, after=0):
    for _ in range(200):
        body = client.get(f"/api/chat/turns/{turn_id}", params={"after": after}, headers=_headers()).json()
        if body["status"] == status:
            return body
        time.sleep(0.01)
    raise AssertionError(f"turn never reached {status}: {body}")


def test_every_chat_route_requires_auth(client):
    assert client.get("/api/chat/state").status_code == 401
    assert client.post("/api/chat", json={"text": "hi"}).status_code == 401
    assert client.get("/api/chat/turns/x").status_code == 401
    assert client.post("/api/chat/turns/x/cancel").status_code == 401


def test_stale_init_data_cannot_post_but_can_read_state(client):
    assert client.get("/api/chat/state", headers=_headers(5 * 3600)).status_code == 200
    assert client.post("/api/chat", json={"text": "hi"}, headers=_headers(5 * 3600)).status_code == 401


def test_send_poll_and_finish(client):
    state = client.get("/api/chat/state", headers=_headers()).json()
    assert state["chat_id"] == _ADMIN_ID and state["busy"] is False and state["active_turn_id"] is None
    r = client.post("/api/chat", json={"text": "salut"}, headers=_headers())
    assert r.status_code == 200
    turn_id = r.json()["turn_id"]
    running = _wait(client, turn_id, "running")
    assert [e["type"] for e in running["events"]][:1] == ["started"]
    assert client.get("/api/chat/state", headers=_headers()).json()["active_turn_id"] == turn_id
    # the tool call is visible while the turn is still running
    for _ in range(200):
        ev = client.get(f"/api/chat/turns/{turn_id}", headers=_headers()).json()["events"]
        if any(e["type"] == "tool_start" for e in ev):
            break
        time.sleep(0.01)
    assert any(e["type"] == "tool_start" and e["name"] == "get_recent_logs" for e in ev)
    client.gate.release.set()
    done = _wait(client, turn_id, "done")
    assert done["reply"] == "am primit: salut"
    tail = client.get(f"/api/chat/turns/{turn_id}", params={"after": len(done["events"]) - 1}, headers=_headers()).json()
    assert [e["type"] for e in tail["events"]] == ["reply"]


def test_cancel_a_running_turn(client):
    turn_id = client.post("/api/chat", json={"text": "lung"}, headers=_headers()).json()["turn_id"]
    _wait(client, turn_id, "running")
    assert client.post(f"/api/chat/turns/{turn_id}/cancel", headers=_headers()).json() == {"cancelled": True}
    _wait(client, turn_id, "cancelled")
    assert client.post(f"/api/chat/turns/{turn_id}/cancel", headers=_headers()).json() == {"cancelled": False}


def test_validation_limits_and_unknown_turn(client):
    assert client.post("/api/chat", json={"text": "   "}, headers=_headers()).status_code == 422
    assert client.post("/api/chat", json={}, headers=_headers()).status_code == 422
    assert client.get("/api/chat/turns/nope", headers=_headers()).status_code == 404
    assert client.post("/api/chat/turns/nope/cancel", headers=_headers()).status_code == 404
    client.post("/api/chat", json={"text": "1"}, headers=_headers())
    client.post("/api/chat", json={"text": "2"}, headers=_headers())
    assert client.post("/api/chat", json={"text": "3"}, headers=_headers()).status_code == 429


def test_503_when_the_bot_has_no_chat_service(monkeypatch):
    monkeypatch.setattr("config.settings.TELEGRAM_BOT_TOKEN", _BOT_TOKEN)
    monkeypatch.setattr(auth_module, "load_admin_chat_id", lambda: _ADMIN_ID)
    with TestClient(create_app(SimpleNamespace())) as c:
        assert c.get("/api/chat/state", headers=_headers()).status_code == 503
        assert c.post("/api/chat", json={"text": "hi"}, headers=_headers()).status_code == 503
