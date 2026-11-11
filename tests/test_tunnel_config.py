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


def test_the_machines_env_switch_overrides_the_committed_file_both_ways():
    from admin_panel.tunnel_config import resolve_enabled
    assert resolve_enabled({"enabled": False}, {"ADMIN_PANEL_TUNNEL": "true"}) is True  # this machine opens it
    assert resolve_enabled({"enabled": True}, {"ADMIN_PANEL_TUNNEL": "off"}) is False
    assert resolve_enabled({"enabled": True}, {"ADMIN_PANEL_TUNNEL": " YES "}) is True
    # missing or unclear: the file decides, and without a file the panel stays loopback-only
    assert resolve_enabled({"enabled": True}, {}) is True
    assert resolve_enabled({"enabled": False}, {"ADMIN_PANEL_TUNNEL": "maybe"}) is False
    assert resolve_enabled({}, {}) is False
