"""tests.test_tunnel_start_guards — admin_panel.tunnel.start's no-op guard clauses.

The real subprocess-launch + Telegram-API path is not practically
unit-testable (external process, real Bot API) — it's covered by the
manual smoke test task at the end of this plan, the same way
admin_panel.server.start's real socket-bind behavior was smoke-tested
rather than unit-tested where a real dependency was unavoidable.
"""
from __future__ import annotations

import pytest

import admin_panel.tunnel as tunnel_module


class _FakeApplication:
    pass


@pytest.mark.asyncio
async def test_start_noop_when_tunnel_disabled(monkeypatch):
    monkeypatch.setattr(tunnel_module, "TUNNEL_ENABLED", False)
    # Must return immediately without touching application.bot or spawning a process.
    await tunnel_module.start(_FakeApplication())


@pytest.mark.asyncio
async def test_start_warns_and_noop_when_no_admin_chat_id(monkeypatch):
    monkeypatch.setattr(tunnel_module, "TUNNEL_ENABLED", True)
    monkeypatch.setattr(tunnel_module, "load_admin_chat_id", lambda: "")
    await tunnel_module.start(_FakeApplication())
