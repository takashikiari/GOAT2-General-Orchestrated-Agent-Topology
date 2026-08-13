"""tests.test_plugin_scanner_admin_wiring — post_init_hook also schedules the
admin panel server as a background task, alongside the existing plugin scan loop."""
from __future__ import annotations

import asyncio

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
    calls = []

    async def _fake_start(registry):
        calls.append(registry)

    async def _fake_loop(registry):
        pass

    # admin_panel.server.start is now imported lazily inside _post_init
    # (see finding #3: the import must not be a hard module-level
    # dependency), so it's no longer a persistent attribute on
    # telegram_interface._plugin_scanner to monkeypatch directly — patch
    # the real source instead.
    monkeypatch.setattr(admin_server_mod, "start", _fake_start)
    monkeypatch.setattr(mod, "_loop", _fake_loop)

    fake_registry = _FakeRegistry()
    hook = mod.post_init_hook(fake_registry)

    async def _run():
        await hook(None)
        await asyncio.sleep(0)

    asyncio.run(_run())

    assert calls == [fake_registry]
