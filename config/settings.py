"""config.settings — single source of truth for GOAT 2.0 configuration.

Non-secret agent config (model, provider, temperature) lives in
``config/agents.toml`` — version-controlled, safe to commit.

Secrets and infrastructure endpoints come from ``.env`` (gitignored),
loaded automatically via python-dotenv. Real environment variables always
take precedence over ``.env`` values, so CI/CD and container deployments
work without a file on disk.

Precedence for agent config (highest → lowest):
  GOAT_AGENT_{ROLE}_* env var  >  agents.toml [role]  >  agents.toml [defaults]

See ``.env.example`` for the full list of supported environment variables.
"""
from __future__ import annotations

import dataclasses
import enum
import os
import tomllib
from pathlib import Path

# Load .env before reading os.environ — real env vars are never overwritten.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env", override=False)
except ImportError:
    pass  # python-dotenv is optional; real env vars still work without it


# ── agents.toml ───────────────────────────────────────────────────────────────

_AGENTS_TOML = Path(__file__).parent / "agents.toml"

_AGENTS_DEFAULTS: dict = {
    "defaults": {
        "model":        "deepseek-v4-flash",
        "provider":     "deepseek",
        "temperature":  0.4,
        "tool_calling": True,
    },
}


def _load_agents_toml() -> dict:
    try:
        with open(_AGENTS_TOML, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return _AGENTS_DEFAULTS


_agents_cfg: dict = _load_agents_toml()


# ── Base env vars ─────────────────────────────────────────────────────────────

API_KEY: str = os.environ.get("DEEPSEEK_API_KEY", "")
MODEL_NAME: str = os.environ.get(
    "MODEL_NAME",
    _agents_cfg.get("defaults", {}).get("model", "deepseek-v4-flash"),
)
BASE_URL: str = os.environ.get("BASE_URL", "https://api.deepseek.com")
TEMPERATURE: float = float(os.environ.get("TEMPERATURE", "0.5"))
MAX_TOKENS: int = int(os.environ.get("MAX_TOKENS", "2048"))
TIMEOUT_SECONDS: float = float(os.environ.get("TIMEOUT_SECONDS", "30.0"))
TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")

# DAG sandbox root — agents running inside a DAG can only access this directory.
DAG_WORKSPACE: Path = Path(
    os.environ.get("DAG_WORKSPACE", str(Path(__file__).parent.parent / "dag_workspace"))
).resolve()


# ── Provider enum ─────────────────────────────────────────────────────────────

class Provider(str, enum.Enum):
    OPENAI   = "openai"
    DEEPSEEK = "deepseek"
    GROQ     = "groq"


PROVIDER_BASE_URLS: dict[Provider, str] = {
    Provider.OPENAI:   "https://api.openai.com/v1",
    Provider.DEEPSEEK: "https://api.deepseek.com",
    Provider.GROQ:     "https://api.groq.com/openai/v1",
}


def _infer_provider(url: str) -> Provider:
    if "deepseek" in url:
        return Provider.DEEPSEEK
    if "groq" in url:
        return Provider.GROQ
    return Provider.OPENAI


def _provider_from_str(value: str, fallback: Provider) -> Provider:
    try:
        return Provider(value.lower())
    except ValueError:
        return fallback


# ── ModelSpec ─────────────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class ModelSpec:
    """Immutable model + provider configuration for one agent role."""

    model_id: str
    provider: Provider = Provider.DEEPSEEK
    tool_calling: bool = True

    def __str__(self) -> str:
        return f"{self.provider.value}/{self.model_id}"


# ── Settings ──────────────────────────────────────────────────────────────────

_AGENT_ROLES: tuple[str, ...] = (
    "planner", "researcher", "coder", "critic",
    "summarizer", "tool_caller", "memory",
)


class Settings:
    """Per-role model configuration.

    Reads config/agents.toml as the base; env vars (GOAT_AGENT_{ROLE}_*)
    override any TOML value at runtime without file edits.
    """

    @property
    def agents(self) -> dict[str, ModelSpec]:
        """Return a ModelSpec for every registered agent role."""
        toml_defaults = _agents_cfg.get("defaults", _AGENTS_DEFAULTS["defaults"])
        default_provider = _provider_from_str(
            toml_defaults.get("provider", "deepseek"),
            _infer_provider(BASE_URL),
        )

        result: dict[str, ModelSpec] = {}
        for role in _AGENT_ROLES:
            key = role.upper()
            role_cfg = _agents_cfg.get(role, {})

            # model: env var > role TOML > defaults TOML > MODEL_NAME
            model_id = (
                os.environ.get(f"GOAT_AGENT_{key}_MODEL")
                or role_cfg.get("model")
                or toml_defaults.get("model")
                or MODEL_NAME
            )

            # provider: env var > role TOML > defaults TOML > inferred from BASE_URL
            prov_env = os.environ.get(f"GOAT_AGENT_{key}_PROVIDER", "")
            prov_toml = role_cfg.get("provider") or toml_defaults.get("provider", "")
            if prov_env:
                provider = _provider_from_str(prov_env, default_provider)
            elif prov_toml:
                provider = _provider_from_str(prov_toml, default_provider)
            else:
                provider = default_provider

            # tool_calling: env var > role TOML > defaults TOML > True
            tc_env = os.environ.get(f"GOAT_AGENT_{key}_TOOL_CALLING", "")
            if tc_env:
                tool_calling = tc_env.lower() not in ("false", "0", "no")
            else:
                tc_toml = role_cfg.get("tool_calling")
                if tc_toml is None:
                    tc_toml = toml_defaults.get("tool_calling", True)
                tool_calling = bool(tc_toml)

            result[role] = ModelSpec(
                model_id=model_id,
                provider=provider,
                tool_calling=tool_calling,
            )
        return result

    def get_agent_temperature(self, role: str, *, default: float = 0.4) -> float:
        """Return temperature for *role*: env var > agents.toml [role] > agents.toml [defaults] > default."""
        env_val = os.environ.get(f"GOAT_AGENT_{role.upper()}_TEMPERATURE")
        if env_val:
            return float(env_val)
        role_cfg = _agents_cfg.get(role, {})
        if "temperature" in role_cfg:
            return float(role_cfg["temperature"])
        toml_defaults = _agents_cfg.get("defaults", {})
        return float(toml_defaults.get("temperature", default))
