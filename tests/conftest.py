"""tests.conftest — what every test shares: no test ever writes the live voice's real minutes file."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _live_usage_file(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICE_LIVE_USAGE_FILE", str(tmp_path / "voice_live_usage.json"))
