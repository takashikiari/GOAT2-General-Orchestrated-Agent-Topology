"""
config.settings — single source of truth for GOAT 2.0 configuration.

All configurable values are read once from environment variables at import
time.  Every other module imports from here; nothing else reads os.environ
directly.  This keeps configuration changes in one place and makes the
dependency graph easy to audit.

Environment variables (with defaults):
    DEEPSEEK_API_KEY    — LLM provider API key (required; no hardcoded default)
    MODEL_NAME          — model identifier to call (default: "deepseek-v4-flash")
    BASE_URL            — provider base URL (default: "https://api.deepseek.com")
    TEMPERATURE         — sampling temperature 0–2   (default: 0.5)
    MAX_TOKENS          — max tokens per LLM response (default: 2048)
    TIMEOUT_SECONDS     — HTTP timeout for LLM calls  (default: 30.0)
    TELEGRAM_BOT_TOKEN  — Telegram bot token from @BotFather (required for bot)

Per-agent overrides (optional env vars):
    GOAT_AGENT_{ROLE}_MODEL          — model id for this role
    GOAT_AGENT_{ROLE}_TOOL_CALLING   — "true"/"false" override
    GOAT_AGENT_{ROLE}_TEMPERATURE    — float override
"""
from __future__ import annotations

import dataclasses
import enum
import os
from pathlib import Path


# ── Base env vars (unchanged) ─────────────────────────────────────────────────

API_KEY: str = os.environ.get("DEEPSEEK_API_KEY", "")
MODEL_NAME: str = os.environ.get("MODEL_NAME", "deepseek-v4-flash")
BASE_URL: str = os.environ.get("BASE_URL", "https://api.deepseek.com")
TEMPERATURE: float = float(os.environ.get("TEMPERATURE", "0.5"))
MAX_TOKENS: int = int(os.environ.get("MAX_TOKENS", "2048"))
TIMEOUT_SECONDS: float = float(os.environ.get("TIMEOUT_SECONDS", "30.0"))
TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")

# DAG sandbox root — agents running inside a DAG can only access this directory.
# Override with GOAT_DAG_WORKSPACE env var (absolute path).
DAG_WORKSPACE: Path = Path(
    os.environ.get("GOAT_DAG_WORKSPACE", "/home/lenovo/workspace/goat2/dag_workspace")
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
    """Per-role model configuration. All values read from env vars."""

    @property
    def agents(self) -> dict[str, ModelSpec]:
        """Return a ModelSpec for every registered agent role."""
        provider = _infer_provider(BASE_URL)
        result: dict[str, ModelSpec] = {}
        for role in _AGENT_ROLES:
            key = role.upper()
            model_id   = os.environ.get(f"GOAT_AGENT_{key}_MODEL", MODEL_NAME)
            tc_env     = os.environ.get(f"GOAT_AGENT_{key}_TOOL_CALLING", "")
            tool_calling = (tc_env.lower() not in ("false", "0", "no")) if tc_env else True
            prov_env   = os.environ.get(f"GOAT_AGENT_{key}_PROVIDER", "")
            if prov_env:
                try:
                    provider_role = Provider(prov_env.lower())
                except ValueError:
                    provider_role = provider
            else:
                provider_role = provider
            result[role] = ModelSpec(
                model_id=model_id,
                provider=provider_role,
                tool_calling=tool_calling,
            )
        return result

    def get_agent_temperature(self, role: str, *, default: float = 0.4) -> float:
        return float(os.environ.get(f"GOAT_AGENT_{role.upper()}_TEMPERATURE", default))
