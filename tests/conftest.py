"""tests.conftest — what every test shares: no test ever writes the live voice's real minutes file."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _live_usage_file(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICE_LIVE_USAGE_FILE", str(tmp_path / "voice_live_usage.json"))


@pytest.fixture(autouse=True)
def _mind_off_unless_asked(monkeypatch):
    """Tests never depend on the production [mind] switch in config/brain.toml: every test starts with the working
    memory off (today's prompt path); the mind tests switch it on themselves."""
    from brain import brain as b
    monkeypatch.setattr(b, "MIND", {**b._MIND_DEFAULTS, "enabled": False})
