"""tests.test_admin_panel_server — create_app wires all four routers together."""
from __future__ import annotations

import socket

import pytest
from fastapi.testclient import TestClient

import admin_panel.admin_config as admin_config
import admin_panel.server as server_module
from admin_panel.server import create_app, start


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


def test_create_app_wires_all_routers():
    client = TestClient(create_app(_FakeRegistry()))
    assert client.get("/api/metrics").status_code == 200
    assert client.get("/api/logs").status_code == 200
    assert client.get("/api/memory/facts").status_code == 200
    assert client.get("/api/memory/working/chat1").status_code == 200
    assert client.get("/api/memory/episodic/chat1").status_code == 200
    assert client.get("/api/conversations").status_code == 200
    assert client.get("/api/conversations/chat1").status_code == 200


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
