"""Per-agent model config — falls back to DEFAULT_MODEL and goat.toml if role env var is unset."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from config.model_catalogue import ModelSpec, get_model
from config.toml_loader import load_toml

__all__ = ["AgentModels"]

_toml = load_toml()


def _key(env_var: str, toml_role: str, role_default: str) -> str:
    """Resolve model key: env var → DEFAULT_MODEL env → toml agent → toml default → role_default."""
    return (
        os.environ.get(env_var)
        or os.environ.get("DEFAULT_MODEL")
        or _toml.agent(toml_role)
        or _toml.model("default")
        or role_default
    )


@dataclass
class AgentModels:
    """Per-agent model keys. Resolution: env → DEFAULT_MODEL → goat.toml → role default."""
    planner:     str = field(default_factory=lambda: _key("AGENT_PLANNER_MODEL",     "planner",     "gpt-4o"))
    researcher:  str = field(default_factory=lambda: _key("AGENT_RESEARCHER_MODEL",  "researcher",  "deepseek-r1"))
    coder:       str = field(default_factory=lambda: _key("AGENT_CODER_MODEL",       "coder",       "deepseek-coder"))
    critic:      str = field(default_factory=lambda: _key("AGENT_CRITIC_MODEL",      "critic",      "llama-3.3-70b"))
    summarizer:  str = field(default_factory=lambda: _key("AGENT_SUMMARIZER_MODEL",  "summarizer",  "llama-3.1-8b"))
    tool_caller: str = field(default_factory=lambda: _key("AGENT_TOOL_CALLER_MODEL", "tool_caller", "gpt-4o-mini"))
    memory:      str = field(default_factory=lambda: _key("AGENT_MEMORY_MODEL",      "memory",      "gpt-4o-mini"))

    def get(self, role: str) -> ModelSpec:
        """Return the ModelSpec for a named agent role; raises ValueError on unknown role."""
        key = getattr(self, role, None)
        if key is None:
            raise ValueError(f"Unknown agent role '{role}'.")
        return get_model(key)
