"""tests.test_tunnel_start_guards — admin_panel.tunnel.start's no-op guard clauses.

The real subprocess-launch + Telegram-API path is not practically
unit-testable (external process, real Bot API) — it's covered by the
manual smoke test task at the end of this plan, the same way
admin_panel.server.start's real socket-bind behavior was smoke-tested
rather than unit-tested where a real dependency was unavoidable.
"""
from __future__ import annotations

import asyncio

import pytest

import admin_panel.tunnel as tunnel_module


class _FakeApplication:
    pass


class _FakeStderr:
    """Fake StreamReader whose readline() behavior is injected per-test."""

    def __init__(self, readline):
        self._readline = readline

    async def readline(self):
        return await self._readline()


class _FakeProcess:
    def __init__(self, readline):
        self.stderr = _FakeStderr(readline)
        self.returncode = None
        self.killed = False

    def kill(self):
        self.killed = True
        self.returncode = -9

    async def wait(self):
        return self.returncode


def _patch_subprocess_exec(monkeypatch, fake_process):
    async def fake_create_subprocess_exec(*args, **kwargs):
        return fake_process

    monkeypatch.setattr(tunnel_module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)


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


@pytest.mark.asyncio
async def test_start_kills_process_on_url_search_timeout(monkeypatch):
    """Finding 1: the timeout path must not leak the spawned cloudflared process."""
    monkeypatch.setattr(tunnel_module, "TUNNEL_ENABLED", True)
    monkeypatch.setattr(tunnel_module, "load_admin_chat_id", lambda: "12345")
    monkeypatch.setattr(tunnel_module, "TUNNEL_TIMEOUT_SECONDS", 0.01)

    async def readline_hangs_forever():
        await asyncio.sleep(3600)

    fake_process = _FakeProcess(readline_hangs_forever)
    _patch_subprocess_exec(monkeypatch, fake_process)

    await tunnel_module.start(_FakeApplication())

    assert fake_process.killed


@pytest.mark.asyncio
async def test_start_catches_limit_overrun_error_and_kills_process(monkeypatch):
    """Finding 2: LimitOverrunError from readline() must be caught, not propagated,
    and must still trigger the Finding-1 cleanup."""
    monkeypatch.setattr(tunnel_module, "TUNNEL_ENABLED", True)
    monkeypatch.setattr(tunnel_module, "load_admin_chat_id", lambda: "12345")
    monkeypatch.setattr(tunnel_module, "TUNNEL_TIMEOUT_SECONDS", 5)

    async def readline_raises_limit_overrun():
        raise asyncio.LimitOverrunError("line too long", 100)

    fake_process = _FakeProcess(readline_raises_limit_overrun)
    _patch_subprocess_exec(monkeypatch, fake_process)

    # Must not raise.
    await tunnel_module.start(_FakeApplication())

    assert fake_process.killed


@pytest.mark.asyncio
async def test_start_kills_process_on_cancellation_during_drain(monkeypatch):
    """Finding 1: cancellation during the post-URL indefinite drain loop must not
    leak the process either — the finally block must run on CancelledError too."""
    monkeypatch.setattr(tunnel_module, "TUNNEL_ENABLED", True)
    monkeypatch.setattr(tunnel_module, "load_admin_chat_id", lambda: "12345")
    monkeypatch.setattr(tunnel_module, "TUNNEL_TIMEOUT_SECONDS", 5)

    lines = iter([b"https://random-example-words.trycloudflare.com\n"])

    async def readline_then_hang():
        try:
            return next(lines)
        except StopIteration:
            await asyncio.sleep(3600)

    fake_process = _FakeProcess(readline_then_hang)
    _patch_subprocess_exec(monkeypatch, fake_process)

    async def fake_set_chat_menu_button(**kwargs):
        return None

    application = _FakeApplication()
    application.bot = type("_FakeBot", (), {"set_chat_menu_button": staticmethod(fake_set_chat_menu_button)})()

    task = asyncio.create_task(tunnel_module.start(application))
    await asyncio.sleep(0.05)  # let it find the URL and enter the indefinite drain
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert fake_process.killed
