"""tests.test_bot_chat_wiring — build_app hands the admin panel a ChatService on the bot's own orchestrator and locks."""
from __future__ import annotations

from unittest.mock import MagicMock

from orchestrator.chat_service import ChatService
from telegram_interface import bot as bot_module


def test_build_app_puts_a_chat_service_on_the_registry(monkeypatch):
    monkeypatch.setattr("config.settings.TELEGRAM_BOT_TOKEN", "123456:ABC-DEF_test-token")
    monkeypatch.setattr(bot_module, "load_admin_chat_id", lambda: "42")
    seen = {}

    class _FakeOrchestrator:
        def __init__(self, **kw):
            seen["orchestrator"] = self

        async def run(self, *a, **kw):
            return "ok"

    monkeypatch.setattr(bot_module, "Orchestrator", _FakeOrchestrator)
    registry = MagicMock()
    bot_module.build_app(registry)
    svc = registry.chat_service
    assert isinstance(svc, ChatService)
    assert svc._run == seen["orchestrator"].run            # the bot's own orchestrator, not a second GOAT
    assert svc._chat_id() == "42"
    assert svc._log_usage is bot_module.usage_probe.log_usage
