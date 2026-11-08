"""tests.test_admin_panel_agents — /api/chat/history (the conversation without agent-coord) and /api/agents (the digests)."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
import redis.exceptions
from fastapi.testclient import TestClient

import admin_panel.routes.agents as agents_route
import admin_panel.telegram_auth as auth_module
from admin_panel.server import create_app
from telegram_interface._coord_poller import format_digest

_BOT_TOKEN = "123456:test-bot-token"
DIGEST = format_digest([{"id": "1-0", "from": "lenovo/goat/superv", "to": "lenovo/goat", "text": "ping"}])


def _headers() -> dict:
    fields = {"user": json.dumps({"id": 42, "first_name": "T"}), "auth_date": str(int(time.time()))}
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", _BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return {"X-Telegram-Init-Data": urlencode(fields)}


class _WM:
    def __init__(self, messages=None, fail=False):
        self.messages, self.fail, self.asked = messages or [], fail, []

    async def get_messages(self, chat_id):
        self.asked.append(chat_id)
        if self.fail:
            raise redis.exceptions.ConnectionError("down")
        return list(self.messages)


def _entries():
    return [
        {"role": "user", "content": "salut", "timestamp": 1, "l3_id": "a"},
        {"role": "assistant", "content": "[Tool calls]\ncalled x({})\n\nbună", "timestamp": 2, "l3_id": "a"},
        {"role": "user", "content": DIGEST, "timestamp": 3, "l3_id": "b"},
        {"role": "assistant", "content": "Am citit ping.", "timestamp": 4, "l3_id": "b"},
    ]


@pytest.fixture
def make(monkeypatch):
    monkeypatch.setattr("config.settings.TELEGRAM_BOT_TOKEN", _BOT_TOKEN)
    monkeypatch.setattr(auth_module, "load_admin_chat_id", lambda: "42")
    monkeypatch.setattr(agents_route, "load_admin_chat_id", lambda: "42")

    def build(wm):
        return TestClient(create_app(SimpleNamespace(working_memory=wm)))
    return build


def test_both_routes_need_auth(make):
    with make(_WM()) as c:
        assert c.get("/api/chat/history").status_code == 401
        assert c.get("/api/agents").status_code == 401


def test_chat_history_has_the_conversation_and_no_agent_coord_turns(make):
    wm = _WM(_entries())
    with make(wm) as c:
        body = c.get("/api/chat/history", headers=_headers()).json()
    assert wm.asked == ["42"]
    assert body["chat_id"] == "42"
    assert [(m["role"], m["content"]) for m in body["messages"]] == [("user", "salut"), ("assistant", "bună")]
    assert body["messages"][1]["tools"] == "called x({})" and body["messages"][0]["tools"] is None
    assert set(body["messages"][0]) == {"role", "content", "tools", "timestamp"}


def test_agents_has_only_the_agent_coord_turns(make):
    with make(_WM(_entries())) as c:
        body = c.get("/api/agents", headers=_headers()).json()
    assert body["chat_id"] == "42" and len(body["turns"]) == 1
    turn = body["turns"][0]
    assert turn["messages"][0]["text"] == "ping" and turn["goat_reply"] == "Am citit ping."
    assert turn["timestamp"] == 3 and turn["reply_timestamp"] == 4


def test_a_storage_failure_is_a_warning_not_a_500(make):
    with make(_WM(fail=True)) as c:
        chat = c.get("/api/chat/history", headers=_headers()).json()
        agents = c.get("/api/agents", headers=_headers()).json()
    assert chat["messages"] == [] and chat["warnings"] == ["Redis unavailable"]
    assert agents["turns"] == [] and agents["warnings"] == ["Redis unavailable"]


def test_no_admin_chat_configured_gives_empty_lists(make, monkeypatch):
    monkeypatch.setattr(agents_route, "load_admin_chat_id", lambda: "")
    with make(_WM(_entries())) as c:
        assert c.get("/api/chat/history", headers=_headers()).json()["messages"] == []
        assert c.get("/api/agents", headers=_headers()).json()["turns"] == []
