"""tests.test_admin_panel_memory — /api/memory/* routes over fake tier doubles."""
from __future__ import annotations

import httpx
import redis.exceptions
from fastapi import FastAPI
from fastapi.testclient import TestClient

from admin_panel.routes.memory import router


class _FakePermanent:
    def __init__(self, facts=None, raise_error=False):
        self._facts = facts or {}
        self._raise = raise_error

    async def get_all_facts(self):
        if self._raise:
            raise httpx.ConnectError("connection refused")
        return self._facts


class _FakeWorking:
    def __init__(self, messages=None, raise_error=False):
        self._messages = messages or {}
        self._raise = raise_error

    async def get_messages(self, chat_id):
        if self._raise:
            raise redis.exceptions.ConnectionError("connection refused")
        return self._messages.get(chat_id, [])


class _FakeEpisodic:
    def __init__(self, recent=None, oldest=None, raise_error=False):
        self._recent = recent or []
        self._oldest = oldest or []
        self._raise = raise_error

    async def get_recent(self, chat_id, limit=20):
        if self._raise:
            raise RuntimeError("chromadb down")
        return self._recent

    async def get_oldest(self, limit, chat_id=None):
        if self._raise:
            raise RuntimeError("chromadb down")
        return self._oldest


class _FakeRegistry:
    def __init__(self, permanent=None, working=None, episodic=None):
        self.permanent_memory = permanent or _FakePermanent()
        self.working_memory = working or _FakeWorking()
        self.episodic_memory = episodic or _FakeEpisodic()


def _client(registry):
    app = FastAPI()
    app.state.registry = registry
    app.include_router(router)
    return TestClient(app)


def test_get_facts_returns_facts():
    resp = _client(_FakeRegistry(permanent=_FakePermanent({"name": "Maria"}))).get("/api/memory/facts")
    assert resp.status_code == 200
    assert resp.json() == {"facts": {"name": "Maria"}}


def test_get_facts_letta_down_returns_error():
    resp = _client(_FakeRegistry(permanent=_FakePermanent(raise_error=True))).get("/api/memory/facts")
    assert resp.status_code == 200
    assert "error" in resp.json()


def test_get_working_returns_messages():
    msgs = {"chat1": [{"role": "user", "content": "hi", "timestamp": 1.0}]}
    resp = _client(_FakeRegistry(working=_FakeWorking(msgs))).get("/api/memory/working/chat1")
    assert resp.json() == {"messages": msgs["chat1"]}


def test_get_working_unknown_chat_returns_empty():
    resp = _client(_FakeRegistry()).get("/api/memory/working/nope")
    assert resp.json() == {"messages": []}


def test_get_working_redis_down_returns_error():
    resp = _client(_FakeRegistry(working=_FakeWorking(raise_error=True))).get("/api/memory/working/chat1")
    assert resp.status_code == 200
    assert "error" in resp.json()


def test_get_episodic_recent_default():
    entries = [{"content": "x", "metadata": {"timestamp": 1.0}}]
    resp = _client(_FakeRegistry(episodic=_FakeEpisodic(recent=entries))).get("/api/memory/episodic/chat1")
    assert resp.json() == {"entries": entries}


def test_get_episodic_oldest_order():
    entries = [{"id": "a", "content": "old", "metadata": {"timestamp": 1.0}}]
    resp = _client(_FakeRegistry(episodic=_FakeEpisodic(oldest=entries))).get("/api/memory/episodic/chat1?order=oldest")
    assert resp.json() == {"entries": entries}


def test_get_episodic_unknown_order_returns_error():
    resp = _client(_FakeRegistry()).get("/api/memory/episodic/chat1?order=sideways")
    assert resp.status_code == 200
    assert "error" in resp.json()


def test_get_episodic_chromadb_down_returns_error():
    resp = _client(_FakeRegistry(episodic=_FakeEpisodic(raise_error=True))).get("/api/memory/episodic/chat1")
    assert "error" in resp.json()
