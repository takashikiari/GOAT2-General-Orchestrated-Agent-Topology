"""tests.test_tunnel_config — admin_panel.tunnel_config constants + toml override."""
from __future__ import annotations

from admin_panel.tunnel_config import TUNNEL_ENABLED, TUNNEL_TIMEOUT_SECONDS


def test_defaults():
    assert TUNNEL_ENABLED is False
    assert TUNNEL_TIMEOUT_SECONDS == 15


def test_load_reads_toml_override(tmp_path, monkeypatch):
    import admin_panel.tunnel_config as mod

    toml_path = tmp_path / "admin_panel.toml"
    toml_path.write_text("[tunnel]\nenabled = true\ntimeout_seconds = 30\n")
    monkeypatch.setattr(mod, "_CONFIG_PATH", toml_path)
    cfg = mod._load().get("tunnel", {})
    assert cfg["enabled"] is True
    assert cfg["timeout_seconds"] == 30
