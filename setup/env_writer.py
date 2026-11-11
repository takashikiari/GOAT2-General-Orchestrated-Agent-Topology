"""setup.env_writer — the .env the setup wizard writes, in the names GOAT actually reads (config/settings.py).

GOAT reads TELEGRAM_BOT_TOKEN, GOAT_{PROVIDER}_API_KEY (openai | deepseek | groq), and picks the provider from BASE_URL
and the model from MODEL_NAME. The wizard used to write TELEGRAM_TOKEN and DEEPSEEK_API_KEY, which nothing reads, and
kept the chosen provider only in goat2.toml, which GOAT does not read for that, so a wizard-made bot started with no
token and no key. No third-party imports here, so the contract test runs without the wizard's own packages
(tests/test_setup_env_contract.py).
"""
from __future__ import annotations

from pathlib import Path

__all__ = ["TOKEN_KEY", "build_env", "read_env"]

TOKEN_KEY = "TELEGRAM_BOT_TOKEN"


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
    provider, BASE_URL + MODEL_NAME (without them GOAT talks to DeepSeek whatever was chosen)."""
    lines = template.splitlines(keepends=True)
    if telegram_token:
        lines = _set(lines, TOKEN_KEY, telegram_token)
    for env_key, value in api_keys.items():
        lines = _set(lines, env_key, value)
    if default_provider:
        lines = _set(lines, "BASE_URL", default_provider["base_url"])
        lines = _set(lines, "MODEL_NAME", default_provider["default_model"])
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
