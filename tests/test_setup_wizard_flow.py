"""tests.test_setup_wizard_flow — the wizard end to end with scripted answers: a refused key is re-entered, Redis is
retried until it answers, and the right files come out (.env 0600, the addresses in config/memory.toml, goat2.toml).

The wizard needs questionary and rich, which CI installs after the tests, so this module is skipped there; the checks
behind each step are tested without them in tests/test_setup_wizard_steps.py.
"""
from __future__ import annotations

import shutil
import tomllib
from pathlib import Path

import pytest

pytest.importorskip("questionary")
pytest.importorskip("rich")

import setup.wizard as wizard  # noqa: E402
from setup import live_checks, memory_toml  # noqa: E402
from setup.service_steps import Outcome  # noqa: E402
from setup.wizard_files import build_goat_toml  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SERVICES = tomllib.loads((ROOT / "setup" / "services.toml").read_text())["services"]
PROVIDERS = tomllib.loads((ROOT / "setup" / "providers.toml").read_text())["providers"]


def test_goat2_toml_keeps_the_required_services_on_and_letta_follows_the_choice():
    template = (ROOT / "setup" / "templates" / "goat2.default.toml").read_text()
    off = tomllib.loads(build_goat_toml(template, "openai", ["openai"], PROVIDERS, [], SERVICES, "42"))
    assert off["services"]["redis"]["enabled"] and off["services"]["chroma"]["enabled"]
    assert off["services"]["letta"]["enabled"] is False
    assert off["providers"]["default"] == "openai" and off["providers"]["openai"]["enabled"]
    assert off["interface"]["telegram"]["admin_chat_id"] == "42"
    on = tomllib.loads(build_goat_toml(template, "openai", ["openai"], PROVIDERS, ["letta"], SERVICES, ""))
    assert on["services"]["letta"]["enabled"] is True


class _Q:
    """questionary stand-in: every prompt pops the next scripted answer (checking which kind of prompt it is)."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.Choice = lambda title, value=None, **kw: value if value is not None else title

    def _next(self, kind, *a, **kw):
        expected, value = self.answers.pop(0)
        assert expected == kind, f"wizard asked {kind} {a[:1]}, the script expected {expected}"
        return type("A", (), {"ask": lambda self_: value})()

    def select(self, *a, **kw):
        return self._next("select", *a)

    def password(self, *a, **kw):
        return self._next("password", *a)

    def text(self, *a, **kw):
        return self._next("text", *a)

    def checkbox(self, *a, **kw):
        return self._next("checkbox", *a)


def test_a_whole_setup_with_a_wrong_key_and_redis_not_started_yet(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    shutil.copy(ROOT / "config" / "memory.toml", tmp_path / "config" / "memory.toml")
    monkeypatch.setattr(wizard, "ROOT", tmp_path)
    monkeypatch.setattr(memory_toml, "MEMORY_TOML", tmp_path / "config" / "memory.toml")
    keys_tried = []
    monkeypatch.setattr(live_checks, "check_api_key", lambda url, key: (keys_tried.append(key), Outcome(
        key == "sk-good", "the key works" if key == "sk-good" else "the key does not answer", "re-enter it"))[1])
    monkeypatch.setattr(live_checks, "check_telegram_token", lambda t: Outcome(True, "bot @goat_bot answers"))
    pings = iter([Outcome(False, "nothing answers at localhost:6379", "start Redis"), Outcome(True, "Redis answers")])
    monkeypatch.setattr(wizard, "redis_ping", lambda url: next(pings))
    q = _Q([
        ("select", "openai"),                      # step 1: provider
        ("password", "sk-wrong"),                  # a key the provider refuses...
        ("select", "Re-enter it"),
        ("password", "sk-good"),                   # ...re-entered
        ("password", "123:tok"),                   # step 2: Telegram token
        ("text", "1912576407"),                    # chat id
        ("text", "redis://localhost:6379/0"),      # step 3: Redis, not started yet...
        ("select", "Retry (you can change the address)"),
        ("text", "redis://localhost:6380/0"),      # ...started on another port, retried
        ("text", "./chroma_data"), ("text", "./chroma_chunks"),
        ("checkbox", []),                          # no optional service
    ])
    monkeypatch.setattr(wizard, "questionary", q)
    wizard.run()
    assert q.answers == [] and keys_tried == ["sk-wrong", "sk-good"]
    env = (tmp_path / ".env").read_text()
    assert "TELEGRAM_BOT_TOKEN=123:tok" in env and "GOAT_OPENAI_API_KEY=sk-good" in env
    assert "BASE_URL=https://api.openai.com/v1" in env and "MODEL_NAME=gpt-4o-mini" in env
    assert (tmp_path / ".env").stat().st_mode & 0o077 == 0
    memory = tomllib.loads((tmp_path / "config" / "memory.toml").read_text())
    assert memory["working"]["storage_url"] == "redis://localhost:6380/0"  # written where GOAT reads it
    assert (tmp_path / "config" / "memory.toml.backup").exists()
    goat = tomllib.loads((tmp_path / "goat2.toml").read_text())
    assert goat["interface"]["telegram"]["admin_chat_id"] == "1912576407" and goat["providers"]["default"] == "openai"
    assert (tmp_path / "chroma_data").is_dir() and (tmp_path / "chroma_chunks").is_dir()


def test_stopping_at_a_failed_step_writes_nothing(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    shutil.copy(ROOT / "config" / "memory.toml", tmp_path / "config" / "memory.toml")
    monkeypatch.setattr(wizard, "ROOT", tmp_path)
    monkeypatch.setattr(memory_toml, "MEMORY_TOML", tmp_path / "config" / "memory.toml")
    monkeypatch.setattr(live_checks, "check_api_key", lambda url, key: Outcome(True, "ok"))
    monkeypatch.setattr(live_checks, "check_telegram_token", lambda t: Outcome(True, "ok"))
    monkeypatch.setattr(wizard, "redis_ping", lambda url: Outcome(False, "nothing answers", "start Redis"))
    monkeypatch.setattr(wizard, "questionary", _Q([
        ("select", "deepseek"), ("password", "sk-x"), ("password", "1:t"), ("text", "1"),
        ("text", "redis://localhost:6379/0"), ("select", "Stop the setup")]))
    with pytest.raises(SystemExit):
        wizard.run()
    assert not (tmp_path / ".env").exists() and not (tmp_path / "goat2.toml").exists()
    assert not (tmp_path / "config" / "memory.toml.backup").exists()
