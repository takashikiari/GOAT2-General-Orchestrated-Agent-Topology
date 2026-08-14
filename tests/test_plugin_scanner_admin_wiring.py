"""tests.test_plugin_scanner_admin_wiring — post_init_hook also schedules the
admin panel server as a background task, alongside the existing plugin scan loop."""
from __future__ import annotations

import asyncio
import time

import admin_panel.server as admin_server_mod
import telegram_interface._plugin_scanner as mod


class _FakeWarmup:
    async def warmup(self):
        pass


class _FakeRegistry:
    def __init__(self):
        self.episodic_memory = _FakeWarmup()
        self.bm25_index = _FakeWarmup()
        self.gliner_extractor = _FakeWarmup()
        self.reranker = None


def test_post_init_schedules_admin_panel_start(monkeypatch):
    import admin_panel.tunnel as tunnel_mod

    calls = []

    async def _fake_start(registry, *, on_started=None):
        calls.append(registry)

    async def _fake_loop(registry):
        pass

    async def _fake_tunnel_start(application):
        pass

    # admin_panel.server.start is now imported lazily inside _post_init
    # (see finding #3: the import must not be a hard module-level
    # dependency), so it's no longer a persistent attribute on
    # telegram_interface._plugin_scanner to monkeypatch directly — patch
    # the real source instead.
    monkeypatch.setattr(admin_server_mod, "start", _fake_start)
    monkeypatch.setattr(mod, "_loop", _fake_loop)
    # admin_panel.tunnel.start must also be mocked, or a real cloudflared
    # tunnel gets launched whenever [tunnel] enabled = true in this
    # machine's config/admin_panel.toml — this test only cares about the
    # admin panel server being scheduled, not the tunnel (that's the
    # sibling test below).
    monkeypatch.setattr(tunnel_mod, "start", _fake_tunnel_start)

    fake_registry = _FakeRegistry()
    post_init, _post_shutdown = mod.post_init_hook(fake_registry)

    async def _run():
        await post_init(None)
        await asyncio.sleep(0)

    asyncio.run(_run())

    assert calls == [fake_registry]


def test_post_init_schedules_admin_tunnel_start(monkeypatch):
    import admin_panel.tunnel as tunnel_mod

    calls = []

    async def _fake_admin_start(registry, *, on_started=None):
        pass

    async def _fake_tunnel_start(application):
        calls.append(application)

    async def _fake_loop(registry):
        pass

    monkeypatch.setattr(admin_server_mod, "start", _fake_admin_start)
    monkeypatch.setattr(tunnel_mod, "start", _fake_tunnel_start)
    monkeypatch.setattr(mod, "_loop", _fake_loop)

    fake_registry = _FakeRegistry()
    post_init, _post_shutdown = mod.post_init_hook(fake_registry)
    fake_application = object()

    async def _run():
        await post_init(fake_application)
        await asyncio.sleep(0)

    asyncio.run(_run())

    assert calls == [fake_application]


def test_post_shutdown_cancels_background_tasks_instead_of_abandoning_them(monkeypatch):
    """Regression test for a real incident: the plugin-scan loop and the
    admin panel/tunnel tasks were fire-and-forget asyncio.create_task calls
    with nothing to cancel them — PTB's own shutdown would finish and close
    the event loop while they were still running, destroying them mid-flight
    ("Task was destroyed but it is pending!" + a cascading "Event loop is
    closed" traceback from uvicorn, observed on every real bot restart).

    Each fake background task here sleeps far longer than this test should
    take (a no-op post_shutdown would still let this test *pass* quickly,
    since nothing here awaits the tasks directly — the actual invariant
    checked is that every task the module scheduled is genuinely `.done()`
    after post_shutdown returns, not merely that the test finished fast).

    The admin-panel fake mimics uvicorn.Server's actual shutdown contract
    (poll a `should_exit` flag) rather than sleeping — post_shutdown
    deliberately does NOT cancel that task outright (see
    admin_panel/server.py:start's docstring for why), so a plain sleep()
    fake would never be woken and this test would hang for real.
    """
    import admin_panel.tunnel as tunnel_mod

    class _FakeUvicornServer:
        def __init__(self):
            self.should_exit = False

    async def _fake_admin_start(registry, *, on_started=None):
        server = _FakeUvicornServer()
        if on_started:
            on_started(server)
        while not server.should_exit:
            await asyncio.sleep(0.05)

    async def _fake_tunnel_start(application):
        await asyncio.sleep(5)

    async def _fake_loop(registry):
        while True:
            await asyncio.sleep(5)

    monkeypatch.setattr(admin_server_mod, "start", _fake_admin_start)
    monkeypatch.setattr(tunnel_mod, "start", _fake_tunnel_start)
    monkeypatch.setattr(mod, "_loop", _fake_loop)

    fake_registry = _FakeRegistry()
    post_init, post_shutdown = mod.post_init_hook(fake_registry)

    async def _run():
        await post_init(object())
        await asyncio.sleep(0)  # let the background tasks actually start
        current = asyncio.current_task()
        background_tasks = [t for t in asyncio.all_tasks() if t is not current]
        assert background_tasks, "expected post_init to have scheduled background tasks"

        await post_shutdown(object())

        still_pending = [t for t in background_tasks if not t.done()]
        assert not still_pending, (
            f"{len(still_pending)}/{len(background_tasks)} background task(s) "
            f"still pending after post_shutdown — they'll be destroyed abruptly "
            f"when the event loop closes instead of cancelled cleanly"
        )

    start = time.monotonic()
    asyncio.run(_run())
    elapsed = time.monotonic() - start
    # Secondary sanity check: cancellation should be near-instant, not the
    # 5s the fakes would take if actually run to completion.
    assert elapsed < 2.0, f"took {elapsed:.1f}s — tasks look waited-out, not cancelled"
