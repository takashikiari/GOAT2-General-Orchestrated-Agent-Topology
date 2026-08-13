"""tests.test_plugin_scanner_admin_wiring — post_init_hook also schedules the
admin panel server as a background task, alongside the existing plugin scan loop."""
from __future__ import annotations

import asyncio

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

    monkeypatch.setattr(mod, "_start_admin_panel", _fake_start)
    monkeypatch.setattr(mod, "_loop", _fake_loop)

    fake_registry = _FakeRegistry()
    hook = mod.post_init_hook(fake_registry)

    async def _run():
        await hook(None)
        await asyncio.sleep(0)

    asyncio.run(_run())

    assert calls == [fake_registry]
