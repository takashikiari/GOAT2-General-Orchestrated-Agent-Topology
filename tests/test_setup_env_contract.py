"""tests.test_setup_env_contract — what the setup wizard writes is what GOAT reads (2026-11-11).

The wizard used to write TELEGRAM_TOKEN and DEEPSEEK_API_KEY while config/settings.py reads TELEGRAM_BOT_TOKEN and
GOAT_DEEPSEEK_API_KEY, so a bot set up by the wizard started with no token and no key. These tests read the wizard's
output back with the real config.settings, in a separate process, so that contract cannot drift silently again.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from config.settings import Provider, _infer_provider
from setup.env_writer import SECRET_MODE, build_env, read_env, write_env_file

ROOT = Path(__file__).resolve().parent.parent
PROVIDERS = tomllib.loads((ROOT / "setup" / "providers.toml").read_text())["providers"]
TEMPLATE = (ROOT / "setup" / "templates" / ".env.example").read_text()


def test_the_wizard_offers_only_providers_goat_can_run_with_the_key_name_goat_reads():
    assert {p["id"] for p in PROVIDERS} == {p.value for p in Provider}
    for p in PROVIDERS:
        assert p["env_key"] == f"GOAT_{p['id'].upper()}_API_KEY"
        assert _infer_provider(p["base_url"]) == Provider(p["id"])  # GOAT picks the provider from BASE_URL
        assert p["default_model"]


def test_the_env_template_uses_the_names_goat_reads_and_keeps_redis_optional():
    lines = {line.split("=", 1)[0] for line in TEMPLATE.splitlines() if "=" in line and not line.startswith("#")}
    assert "TELEGRAM_BOT_TOKEN" in lines and "TELEGRAM_TOKEN" not in lines
    assert {f"GOAT_{p.value.upper()}_API_KEY" for p in Provider} <= lines
    assert not any(k.endswith("_API_KEY") and not k.startswith("GOAT_") for k in lines)
    assert "REDIS_URL" not in lines  # optional: only as a comment


def _what_goat_reads(env_file: Path) -> dict:
    """config.settings in a fresh process, with exactly the variables of ``env_file``."""
    env = {k: v for k, v in os.environ.items()
           if not (k.endswith("_API_KEY") or k in ("TELEGRAM_BOT_TOKEN", "BASE_URL", "MODEL_NAME"))}
    env.update(read_env(env_file))
    code = ("import json; from config import settings as s; "
            "p = s._infer_provider(s.BASE_URL); "
            "print(json.dumps({'token': s.TELEGRAM_BOT_TOKEN, 'provider': p.value, 'key': s.get_api_key(p), "
            "'model': s.MODEL_NAME}))")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("provider", PROVIDERS, ids=lambda p: p["id"])
def test_a_bot_set_up_by_the_wizard_gets_its_token_key_provider_and_model(provider, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(build_env(TEMPLATE, "123:telegram-token", {provider["env_key"]: f"key-{provider['id']}"}, provider))
    read = _what_goat_reads(env_file)
    assert read == {"token": "123:telegram-token", "provider": provider["id"], "key": f"key-{provider['id']}",
                    "model": provider["default_model"]}


def test_a_value_already_in_the_template_is_replaced_not_duplicated():
    out = build_env("TELEGRAM_BOT_TOKEN=\nGOAT_OPENAI_API_KEY=\n", "t", {"GOAT_OPENAI_API_KEY": "k"}, None)
    assert out.count("TELEGRAM_BOT_TOKEN=") == 1 and "TELEGRAM_BOT_TOKEN=t\n" in out and "GOAT_OPENAI_API_KEY=k\n" in out
    assert "BASE_URL" not in out  # no default provider chosen: GOAT's own defaults stay


def test_the_env_and_its_backup_are_readable_by_their_owner_only(tmp_path):
    env = tmp_path / ".env"
    assert write_env_file(env, "TELEGRAM_BOT_TOKEN=a\n") is None  # nothing to back up yet
    assert env.stat().st_mode & 0o077 == 0
    env.chmod(0o644)  # an old .env that anyone on the machine could read
    backup = write_env_file(env, "TELEGRAM_BOT_TOKEN=b\n")
    assert backup == tmp_path / ".env.backup" and backup.read_text() == "TELEGRAM_BOT_TOKEN=a\n"
    for f in (env, backup):
        assert f.stat().st_mode & 0o777 == SECRET_MODE, oct(f.stat().st_mode)
    assert env.read_text() == "TELEGRAM_BOT_TOKEN=b\n"
