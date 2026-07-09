"""tests.test_telegram_config — telegram_interface.telegram_config constants.

2026-07-09: moved bot.py's _UPDATE_DEDUPE_TTL_SECONDS out of a hardcoded
module literal into config/telegram.toml. (_MAX_TG_LEN stays hardcoded —
it's Telegram's protocol message-length limit, not an operational tunable.)
"""
from __future__ import annotations

from telegram_interface.telegram_config import UPDATE_DEDUPE_TTL_SECONDS


def test_dedupe_ttl_default():
    assert UPDATE_DEDUPE_TTL_SECONDS == 86400


def test_load_reads_toml_override(tmp_path, monkeypatch):
    import telegram_interface.telegram_config as mod

    toml_path = tmp_path / "telegram.toml"
    toml_path.write_text("[dedupe]\nttl_seconds = 123\n")
    monkeypatch.setattr(mod, "_CONFIG_PATH", toml_path)
    cfg = mod._load().get("dedupe", {})
    assert cfg["ttl_seconds"] == 123
