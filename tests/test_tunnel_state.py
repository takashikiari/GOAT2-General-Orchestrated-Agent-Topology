"""tests.test_tunnel_state — what the panel knows about its own Cloudflare tunnel (shown in the dashboard)."""
from __future__ import annotations

import asyncio

import pytest

import admin_panel.tunnel as tunnel_module
from admin_panel import tunnel_state


@pytest.fixture(autouse=True)
def _reset_state():
    tunnel_state.set_state("off")
    yield
    tunnel_state.set_state("off")


class _FakeStderr:
    def __init__(self, readline):
        self._readline = readline

    async def readline(self):
        return await self._readline()


class _FakeProcess:
    def __init__(self, readline):
        self.stderr = _FakeStderr(readline)
        self.returncode = None

    def kill(self):
        self.returncode = -9

    async def wait(self):
        return self.returncode


class _App:
    def __init__(self):
        self.bot = type("_Bot", (), {"set_chat_menu_button": staticmethod(self._menu)})()
        self.menu_calls: list[str] = []

    async def _menu(self, **kwargs):
        self.menu_calls.append(kwargs["menu_button"].web_app.url)


def _enable(monkeypatch, timeout=5):
    monkeypatch.setattr(tunnel_module, "TUNNEL_ENABLED", True)
    monkeypatch.setattr(tunnel_module, "load_admin_chat_id", lambda: "12345")
    monkeypatch.setattr(tunnel_module, "TUNNEL_TIMEOUT_SECONDS", timeout)


def _spawn(monkeypatch, readline):
    process = _FakeProcess(readline)

    async def fake_exec(*args, **kwargs):
        return process

    monkeypatch.setattr(tunnel_module.asyncio, "create_subprocess_exec", fake_exec)
    return process


def test_state_starts_off_and_get_returns_a_copy():
    state = tunnel_state.get_state()
    assert state["status"] == "off" and state["url"] is None
    state["status"] = "ready"
    assert tunnel_state.get_state()["status"] == "off"


def test_set_state_records_url_detail_and_time():
    tunnel_state.set_state("ready", url="https://x-y.trycloudflare.com", detail="ok")
    state = tunnel_state.get_state()
    assert state["status"] == "ready" and state["url"] == "https://x-y.trycloudflare.com" and state["detail"] == "ok"
    assert isinstance(state["since"], float)
    with pytest.raises(ValueError):
        tunnel_state.set_state("nonsense")


@pytest.mark.asyncio
async def test_disabled_in_config_means_off(monkeypatch):
    monkeypatch.setattr(tunnel_module, "TUNNEL_ENABLED", False)
    await tunnel_module.start(_App())
    state = tunnel_state.get_state()
    assert state["status"] == "off" and "config" in state["detail"]


@pytest.mark.asyncio
async def test_no_admin_chat_means_off(monkeypatch):
    monkeypatch.setattr(tunnel_module, "TUNNEL_ENABLED", True)
    monkeypatch.setattr(tunnel_module, "load_admin_chat_id", lambda: "")
    await tunnel_module.start(_App())
    assert tunnel_state.get_state()["status"] == "off"


@pytest.mark.asyncio
async def test_missing_cloudflared_means_unavailable(monkeypatch):
    _enable(monkeypatch)

    async def boom(*args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(tunnel_module.asyncio, "create_subprocess_exec", boom)
    await tunnel_module.start(_App())
    state = tunnel_state.get_state()
    assert state["status"] == "unavailable" and state["url"] is None and "PATH" in state["detail"]


@pytest.mark.asyncio
async def test_ready_with_the_url_and_down_when_the_tunnel_stops(monkeypatch):
    _enable(monkeypatch)
    lines = iter([b"INF noise\n", b"INF |  https://random-example-words.trycloudflare.com  |\n"])

    async def readline():
        try:
            return next(lines)
        except StopIteration:
            await asyncio.sleep(3600)

    _spawn(monkeypatch, readline)
    app = _App()
    task = asyncio.create_task(tunnel_module.start(app))
    await asyncio.sleep(0.05)
    state = tunnel_state.get_state()
    assert state["status"] == "ready" and state["url"] == "https://random-example-words.trycloudflare.com"
    assert app.menu_calls == ["https://random-example-words.trycloudflare.com"]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    state = tunnel_state.get_state()
    assert state["status"] == "down" and state["url"] is None   # a dead URL must not stay on the dashboard


@pytest.mark.asyncio
async def test_starting_is_visible_while_waiting_for_the_url(monkeypatch):
    _enable(monkeypatch)

    async def readline():
        await asyncio.sleep(3600)

    _spawn(monkeypatch, readline)
    task = asyncio.create_task(tunnel_module.start(_App()))
    await asyncio.sleep(0.05)
    assert tunnel_state.get_state()["status"] == "starting"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_timeout_and_early_exit_mean_down(monkeypatch):
    _enable(monkeypatch, timeout=0.01)

    async def hang():
        await asyncio.sleep(3600)

    _spawn(monkeypatch, hang)
    await tunnel_module.start(_App())
    assert tunnel_state.get_state()["status"] == "down" and "URL" in tunnel_state.get_state()["detail"]

    tunnel_state.set_state("off")
    _enable(monkeypatch)

    async def closed():
        return b""

    _spawn(monkeypatch, closed)
    await tunnel_module.start(_App())
    assert tunnel_state.get_state()["status"] == "down"
