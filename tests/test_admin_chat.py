"""tests.test_admin_chat — config.admin_chat.load_admin_chat_id."""
from __future__ import annotations

from config.admin_chat import load_admin_chat_id


def test_returns_empty_string_when_goat2_toml_missing(tmp_path, monkeypatch):
    import config.admin_chat as mod

    monkeypatch.setattr(mod, "_ROOT", tmp_path)
    assert mod.load_admin_chat_id() == ""


def test_reads_admin_chat_id_from_goat2_toml(tmp_path, monkeypatch):
    import config.admin_chat as mod

    (tmp_path / "goat2.toml").write_text(
        '[interface.telegram]\nadmin_chat_id = "777"\n'
    )
    monkeypatch.setattr(mod, "_ROOT", tmp_path)
    assert mod.load_admin_chat_id() == "777"


def test_returns_empty_string_on_malformed_toml(tmp_path, monkeypatch):
    import config.admin_chat as mod

    (tmp_path / "goat2.toml").write_text("not valid toml {{{")
    monkeypatch.setattr(mod, "_ROOT", tmp_path)
    assert mod.load_admin_chat_id() == ""
