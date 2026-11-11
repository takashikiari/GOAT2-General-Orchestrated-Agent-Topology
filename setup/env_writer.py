"""setup.env_writer — the .env the setup wizard writes, in the names GOAT actually reads (config/settings.py).

GOAT reads TELEGRAM_BOT_TOKEN, GOAT_{PROVIDER}_API_KEY (openai | deepseek | groq), and picks the provider from BASE_URL
and the model from MODEL_NAME. The wizard used to write TELEGRAM_TOKEN and DEEPSEEK_API_KEY, which nothing reads, and
kept the chosen provider only in goat2.toml, which GOAT does not read for that, so a wizard-made bot started with no
token and no key. No third-party imports here, so the contract test runs without the wizard's own packages
(tests/test_setup_env_contract.py).
"""
from __future__ import annotations

import os
from pathlib import Path

__all__ = ["TOKEN_KEY", "SECRET_MODE", "STRONG_ROLES", "build_env", "read_env", "write_env_file"]

SECRET_MODE = 0o600  # .env holds the bot token and API keys: readable and writable by its owner only

TOKEN_KEY = "TELEGRAM_BOT_TOKEN"
STRONG_ROLES = ("planner", "coder")  # the roles that get the provider's strong_model; the others follow MODEL_NAME


def _set(lines: list[str], key: str, value: str) -> list[str]:
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = f"{key}={value}\n"
            return lines
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    lines.append(f"{key}={value}\n")
    return lines


def build_env(template: str, telegram_token: str, api_keys: dict[str, str], default_provider: dict | None) -> str:
    """The .env text: the template with the token, the API keys (by their ``GOAT_*`` names) and, for the default
    provider, BASE_URL + MODEL_NAME (its fast_model) + GOAT_AGENT_{PLANNER,CODER}_MODEL (its strong_model). Without
    them GOAT talks to DeepSeek whatever was chosen; config/agents.toml names no provider or model, so every role
    follows these."""
    lines = template.splitlines(keepends=True)
    if telegram_token:
        lines = _set(lines, TOKEN_KEY, telegram_token)
    for env_key, value in api_keys.items():
        lines = _set(lines, env_key, value)
    if default_provider:
        lines = _set(lines, "BASE_URL", default_provider["base_url"])
        lines = _set(lines, "MODEL_NAME", default_provider["fast_model"])
        for role in STRONG_ROLES:
            lines = _set(lines, f"GOAT_AGENT_{role.upper()}_MODEL", default_provider["strong_model"])
    return "".join(lines)


def read_env(path: Path) -> dict[str, str]:
    """``KEY=value`` lines of a .env file (comments and blank values skipped)."""
    out = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            if value.strip():
                out[key.strip()] = value.strip()
    return out


def _write_private(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` with SECRET_MODE, also when the file already existed with wider permissions
    (O_CREAT's mode only applies to a new file, so the mode is set on the open descriptor as well)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, SECRET_MODE)
    try:
        os.fchmod(fd, SECRET_MODE)
    except BaseException:
        os.close(fd)
        raise
    with os.fdopen(fd, "w", encoding="utf-8") as f:  # closes fd; writes everything
        f.write(text)


def write_env_file(path: Path, text: str) -> Path | None:
    """Write the .env privately. An existing one is first copied to ``.env.backup``, privately too (a plain copy would
    keep the old file's permissions). Returns the backup's path, or None when there was nothing to back up."""
    path = Path(path)
    backup = None
    if path.exists():
        backup = path.with_name(path.name + ".backup")
        _write_private(backup, path.read_text(encoding="utf-8"))  # never briefly readable by others
    _write_private(path, text)
    return backup
