"""tests.test_setup_md_contract — SETUP.md's environment variables are the ones GOAT reads, with GOAT's real defaults.

SETUP.md used to tell people to set DEEPSEEK_API_KEY (and ANTHROPIC/OPENROUTER/GEMINI keys GOAT cannot use) while
config/settings.py reads GOAT_{PROVIDER}_API_KEY for openai | deepseek | groq only. This reads the document's table and
its example block and checks them against the code, so the documentation cannot drift from it again.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

from config.settings import Provider

ROOT = Path(__file__).resolve().parent.parent
DOC = (ROOT / "SETUP.md").read_text()
# where the documented variables are read: the settings module, and the log setup for GOAT_LOG_DIR
SOURCES = (ROOT / "config" / "settings.py").read_text() + (ROOT / "utils" / "logging" / "setup.py").read_text()


def _table() -> dict[str, str]:
    """``{variable: default}`` from the 'Environment variables' table."""
    section = DOC.split("## Environment variables", 1)[1].split("\n## ", 1)[0]
    rows = re.findall(r"^\| `([A-Z_{}]+)` \| [^|]+\| ([^|]+)\|", section, flags=re.M)
    return {name: default.strip().strip("`") for name, default in rows}


def test_every_variable_in_the_table_is_read_by_the_code_and_the_key_row_names_the_real_providers():
    table = _table()
    assert "GOAT_{PROVIDER}_API_KEY" in table and "TELEGRAM_BOT_TOKEN" in table
    for name in table:
        if name != "GOAT_{PROVIDER}_API_KEY":
            assert f'"{name}"' in SOURCES, f"{name} is documented but not read"
    key_row = next(line for line in DOC.splitlines() if line.startswith("| `GOAT_{PROVIDER}_API_KEY`"))
    assert set(re.findall(r"`GOAT_([A-Z]+)_API_KEY`", key_row)) == {p.value.upper() for p in Provider}


def test_no_key_name_goat_does_not_read_is_suggested_anywhere():
    names = set(re.findall(r"\b([A-Z]+_API_KEY)\b", DOC))
    allowed = {f"GOAT_{p.value.upper()}_API_KEY" for p in Provider}
    assert names <= allowed, f"SETUP.md names keys GOAT never reads: {sorted(names - allowed)}"
    assert "TELEGRAM_TOKEN" not in re.sub(r"TELEGRAM_BOT_TOKEN", "", DOC)


def test_the_documented_defaults_are_goats_real_defaults():
    table = _table()
    clean = {k: v for k, v in os.environ.items()
             if k not in ("MODEL_NAME", "BASE_URL", "TEMPERATURE", "MAX_TOKENS", "TIMEOUT_SECONDS")}
    code = ("import json; from config import settings as s; print(json.dumps({'MODEL_NAME': s.MODEL_NAME, "
            "'BASE_URL': s.BASE_URL, 'TEMPERATURE': s.TEMPERATURE, 'MAX_TOKENS': s.MAX_TOKENS, "
            "'TIMEOUT_SECONDS': s.TIMEOUT_SECONDS}))")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=clean, capture_output=True, text=True, check=True)
    real = json.loads(out.stdout.strip().splitlines()[-1])
    for name, value in real.items():
        assert table[name] == str(value), f"SETUP.md says {name} defaults to {table[name]!r}, the code uses {value!r}"


SERVICES = tomllib.loads((ROOT / "setup" / "services.toml").read_text())["services"]
AGENTS = tomllib.loads((ROOT / "config" / "agents.toml").read_text())


def test_redis_and_chromadb_are_required_and_letta_is_the_only_optional_service():
    required = {s["id"]: s["required"] for s in SERVICES}
    assert required["redis"] is True and required["chroma"] is True and required["letta"] is False
    assert [s["id"] for s in SERVICES if not s["required"]] == ["letta"]
    # no env_key that nothing reads (the Redis/Letta addresses come from config/memory.toml)
    assert {s.get("env_key") for s in SERVICES} - {None} == {"TELEGRAM_BOT_TOKEN"}


def test_the_pre_flight_checks_require_redis_at_goats_real_address_and_chromadb():
    import importlib.util
    spec = importlib.util.spec_from_file_location("setup_checks", ROOT / "setup" / "checks.py")  # as run.sh runs it
    checks = importlib.util.module_from_spec(spec)
    sys.modules["setup_checks"] = checks  # dataclasses look their module up there
    spec.loader.exec_module(checks)
    assert checks.redis_url_from_config() == tomllib.loads((ROOT / "config" / "memory.toml").read_text())["working"]["storage_url"]
    down = checks.check_redis("redis://127.0.0.1:1/0")  # nothing listens on port 1
    assert (down.ok, down.required) == (False, True) and "enabled = false" not in down.fix_hint
    assert checks.check_chroma().required is True
    assert checks.print_results([down]) is False  # a required failure stops run.sh


def test_no_role_in_agents_toml_pins_a_provider_or_model_and_goat_still_defaults_to_today():
    for section, cfg in AGENTS.items():
        assert "provider" not in cfg and "model" not in cfg, section
    clean = {k: v for k, v in os.environ.items()
             if not (k.startswith("GOAT_AGENT_") or k in ("MODEL_NAME", "BASE_URL"))}
    code = ("import json; from config import settings as s; "
            "print(json.dumps([s.MODEL_NAME, sorted({(m.provider.value, m.model_id) for m in s.Settings().agents.values()})]))")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=clean, capture_output=True, text=True, check=True)
    model, roles = json.loads(out.stdout.strip().splitlines()[-1])
    # nothing set (like the live .env today): the same model and provider as before, for GOAT and every role
    assert model == "deepseek-v4-flash" and roles == [["deepseek", "deepseek-v4-flash"]]
