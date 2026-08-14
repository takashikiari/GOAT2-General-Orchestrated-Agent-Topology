"""tests.test_admin_config — admin_panel.admin_config constants + toml override."""
from __future__ import annotations

from admin_panel.admin_config import ADMIN_HOST, ADMIN_PORT


def test_defaults():
    assert ADMIN_HOST == "127.0.0.1"
    assert ADMIN_PORT == 8765


def test_load_reads_toml_override(tmp_path, monkeypatch):
    import admin_panel.admin_config as mod

    toml_path = tmp_path / "admin_panel.toml"
    toml_path.write_text('[server]\nhost = "0.0.0.0"\nport = 9999\n')
    monkeypatch.setattr(mod, "_CONFIG_PATH", toml_path)
    cfg = mod._load().get("server", {})
    assert cfg["host"] == "0.0.0.0"
    assert cfg["port"] == 9999
