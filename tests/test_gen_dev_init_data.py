"""tests.test_gen_dev_init_data — scripts.gen_dev_init_data.generate_dev_init_data."""
from __future__ import annotations

import pytest

from admin_panel.telegram_auth import verify_init_data
from scripts.gen_dev_init_data import generate_dev_init_data


def test_generates_init_data_verifiable_by_the_real_backend_check(monkeypatch):
    monkeypatch.setattr("config.settings.TELEGRAM_BOT_TOKEN", "123456:test-bot-token")
    monkeypatch.setattr("scripts.gen_dev_init_data.load_admin_chat_id", lambda: "777")

    init_data = generate_dev_init_data()

    result = verify_init_data(init_data, "123456:test-bot-token", max_age_seconds=86400)
    assert result is not None
    assert result["user"]["id"] == 777


def test_raises_when_admin_chat_id_not_configured(monkeypatch):
    monkeypatch.setattr("scripts.gen_dev_init_data.load_admin_chat_id", lambda: "")
    with pytest.raises(SystemExit):
        generate_dev_init_data()


def test_raises_when_bot_token_not_configured(monkeypatch):
    monkeypatch.setattr("scripts.gen_dev_init_data.load_admin_chat_id", lambda: "777")
    monkeypatch.setattr("config.settings.TELEGRAM_BOT_TOKEN", "")
    with pytest.raises(SystemExit):
        generate_dev_init_data()
