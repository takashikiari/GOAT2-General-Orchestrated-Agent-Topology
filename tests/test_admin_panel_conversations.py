"""tests.test_admin_panel_conversations — /api/conversations routes over fake tier doubles.

Unlike memory.py's routes, these merge two backends per response, so a single
backend failing surfaces as a `warnings` list alongside whatever data the
other backend provided — never a blanket {"error": ...}.
"""
from __future__ import annotations

import redis.exceptions
from fastapi import FastAPI
from fastapi.testclient import TestClient

from admin_panel.routes.conversations import router


class _FakeWorking:
    def __init__(self, chat_ids=None, messages=None, raise_error=False):
        self._chat_ids = chat_ids or []
        self._messages = messages or {}
        self._raise = raise_error

    async def list_chat_ids(self):
        if self._raise:
            raise redis.exceptions.ConnectionError("down")
        return self._chat_ids

    async def get_messages(self, chat_id):
        if self._raise:
            raise redis.exceptions.ConnectionError("down")
        return self._messages.get(chat_id, [])


class _FakeEpisodic:
    def __init__(self, chat_ids=None, recent=None, raise_error=False):
        self._chat_ids = chat_ids or []
        self._recent = recent or []
        self._raise = raise_error

    async def list_chat_ids(self):
        if self._raise:
            raise RuntimeError("chromadb down")
        return self._chat_ids

    async def get_recent(self, chat_id, limit=20):
        if self._raise:
            raise RuntimeError("chromadb down")
        return self._recent


class _FakeRegistry:
    def __init__(self, working=None, episodic=None):
        self.working_memory = working or _FakeWorking()
        self.episodic_memory = episodic or _FakeEpisodic()


def _client(registry):
    app = FastAPI()
    app.state.registry = registry
    app.include_router(router)
    return TestClient(app)


def test_list_conversations_unions_active_and_archived():
    working = _FakeWorking(chat_ids=["a", "b"])
    episodic = _FakeEpisodic(chat_ids=["b", "c"])
    resp = _client(_FakeRegistry(working=working, episodic=episodic)).get("/api/conversations")
    body = resp.json()
    assert "warnings" not in body
    statuses = {c["chat_id"]: c["status"] for c in body["conversations"]}
    assert statuses == {"a": "active", "b": "active", "c": "archived_only"}


def test_list_conversations_redis_down_still_returns_archived():
    working = _FakeWorking(raise_error=True)
    episodic = _FakeEpisodic(chat_ids=["c"])
    resp = _client(_FakeRegistry(working=working, episodic=episodic)).get("/api/conversations")
    body = resp.json()
    assert body["conversations"] == [{"chat_id": "c", "status": "archived_only"}]
    assert "warnings" in body and len(body["warnings"]) == 1


def test_list_conversations_both_down_returns_empty_with_two_warnings():
    resp = _client(_FakeRegistry(working=_FakeWorking(raise_error=True), episodic=_FakeEpisodic(raise_error=True))).get("/api/conversations")
    body = resp.json()
    assert body["conversations"] == []
    assert len(body["warnings"]) == 2


def test_get_conversation_merges_l2_and_l3_sorted_by_timestamp():
    working = _FakeWorking(messages={"chat1": [{"role": "user", "content": "hi", "timestamp": 2.0}]})
    episodic = _FakeEpisodic(recent=[{"content": "archived note", "metadata": {"timestamp": 1.0}}])
    resp = _client(_FakeRegistry(working=working, episodic=episodic)).get("/api/conversations/chat1")
    body = resp.json()
    assert body["chat_id"] == "chat1"
    assert [e["tier"] for e in body["timeline"]] == ["L3", "L2"]
    assert body["timeline"][0]["content"] == "archived note"
    assert body["timeline"][1]["content"] == "hi"


def test_get_conversation_partial_failure_still_returns_other_tier():
    working = _FakeWorking(raise_error=True)
    episodic = _FakeEpisodic(recent=[{"content": "archived note", "metadata": {"timestamp": 1.0}}])
    resp = _client(_FakeRegistry(working=working, episodic=episodic)).get("/api/conversations/chat1")
    body = resp.json()
    assert len(body["timeline"]) == 1
    assert "warnings" in body
