"""
GOAT 2.0 — environment-based configuration.
Primary local config: config/goat.toml. Env vars always override toml values.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from config.agent_models import AgentModels
from config.api_keys import APIKeys, PROVIDER_BASE_URLS
from config.model_catalogue import ModelSpec, Provider, MODELS, get_model
from config.toml_loader import load_toml

__all__ = ["Settings", "settings", "AgentModels", "APIKeys", "PROVIDER_BASE_URLS",
           "ModelSpec", "Provider", "MODELS", "get_model"]

_toml = load_toml()


def _e(env_var: str, toml_val: str = "", default: str = "") -> str:
    """Resolve: env var → toml value → default. Pure."""
    return os.environ.get(env_var) or toml_val or default


@dataclass
class LettaConfig:
    base_url:           str = field(default_factory=lambda: _e("LETTA_BASE_URL",    _toml.memory_str("letta_base_url"),    "http://localhost:8283"))
    api_key:            str = field(default_factory=lambda: _e("LETTA_API_KEY",     _toml.memory_str("letta_api_key")))
    embed_model:        str = field(default_factory=lambda: _e("LETTA_EMBED_MODEL", _toml.memory_str("letta_embed_model"), "openai/text-embedding-ada-002"))
    llm_model:          str = field(default_factory=lambda: _e("LETTA_LLM_MODEL",   _toml.memory_str("letta_llm_model"),   "openai/gpt-4o-mini"))
    memory_token_limit: int = field(default_factory=lambda: int(
        os.environ.get("LETTA_MEMORY_TOKEN_LIMIT") or _toml.memory_int("letta_token_limit", 4096)
    ))

    @property
    def headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h


@dataclass
class SupervisorConfig:
    model_key:    str  = field(default_factory=lambda: _e("SUPERVISOR_MODEL",   _toml.model("supervisor"), "gpt-4o"))
    max_turns:    int  = field(default_factory=lambda: int(_e("SUPERVISOR_MAX_TURNS",    "", "20")))
    max_workers:  int  = field(default_factory=lambda: int(_e("SUPERVISOR_MAX_WORKERS",  "", "5")))
    turn_timeout: int  = field(default_factory=lambda: int(_e("SUPERVISOR_TURN_TIMEOUT", "", "120")))
    verbose:      bool = field(default_factory=lambda: _e("SUPERVISOR_VERBOSE", "", "false").lower() == "true")

    @property
    def model(self) -> ModelSpec:
        return get_model(self.model_key)


@dataclass
class Settings:
    api_keys:         APIKeys          = field(default_factory=APIKeys)
    letta:            LettaConfig      = field(default_factory=LettaConfig)
    supervisor:       SupervisorConfig = field(default_factory=SupervisorConfig)
    agents:           AgentModels      = field(default_factory=AgentModels)
    log_level:        str              = field(default_factory=lambda: _e("LOG_LEVEL", "", "INFO"))
    env:              str              = field(default_factory=lambda: _e("ENV", "", "development"))
    default_model:    str              = field(default_factory=lambda: _e("DEFAULT_MODEL",    _toml.model("default")))
    default_provider: str              = field(default_factory=lambda: _e("DEFAULT_PROVIDER", _toml.model("provider")))

    @property
    def is_production(self) -> bool:
        return self.env.lower() == "production"

    def validate(self) -> None:
        """Raise EnvironmentError for any missing required credentials."""
        _ROLES = ("planner", "researcher", "coder", "critic", "summarizer", "tool_caller", "memory")
        providers: set[Provider] = {self.agents.get(r).provider for r in _ROLES}
        providers.add(self.supervisor.model.provider)
        key_map = {Provider.OPENAI: self.api_keys.openai,
                   Provider.DEEPSEEK: self.api_keys.deepseek,
                   Provider.GROQ: self.api_keys.groq}
        errors = [f"  {p.value.upper()}_API_KEY is not set" for p in providers if not key_map[p]]
        if self.default_provider and self.default_provider not in {p.value for p in Provider}:
            errors.append(f"  DEFAULT_PROVIDER='{self.default_provider}' unknown; valid: {[p.value for p in Provider]}")
        if errors:
            raise EnvironmentError("GOAT 2.0 configuration errors:\n" + "\n".join(errors))


settings = Settings()
