"""tests.test_auth_config — admin_panel.auth_config constants + toml override."""
from __future__ import annotations

from admin_panel.auth_config import AUTH_MAX_AGE_SECONDS


def test_default():
    assert AUTH_MAX_AGE_SECONDS == 86400


def test_load_reads_toml_override(tmp_path, monkeypatch):
    import admin_panel.auth_config as mod

    toml_path = tmp_path / "admin_panel.toml"
    toml_path.write_text("[auth]\nmax_age_seconds = 60\n")
    monkeypatch.setattr(mod, "_CONFIG_PATH", toml_path)
    cfg = mod._load().get("auth", {})
    assert cfg["max_age_seconds"] == 60
