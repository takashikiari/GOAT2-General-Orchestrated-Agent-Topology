"""API credentials and provider base URLs — env var takes precedence over goat.toml."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Final

from config.model_catalogue import Provider
from config.toml_loader import load_toml

__all__ = ["APIKeys", "PROVIDER_BASE_URLS"]

_toml = load_toml()


def _api_key(env_var: str, toml_provider: str) -> str:
    """Resolve API key: env var → goat.toml [api_keys] → empty string. Pure."""
    return os.environ.get(env_var) or _toml.api_key(toml_provider)


PROVIDER_BASE_URLS: Final[dict[str, str]] = {
    Provider.OPENAI.value:   "https://api.openai.com/v1",
    Provider.DEEPSEEK.value: "https://api.deepseek.com/v1",
    Provider.GROQ.value:     "https://api.groq.com/openai/v1",
}


@dataclass
class APIKeys:
    """API credentials per provider — env var takes precedence over goat.toml [api_keys]."""
    openai:   str = field(default_factory=lambda: _api_key("OPENAI_API_KEY",   "openai"))
    deepseek: str = field(default_factory=lambda: _api_key("DEEPSEEK_API_KEY", "deepseek"))
    groq:     str = field(default_factory=lambda: _api_key("GROQ_API_KEY",     "groq"))

    def for_provider(self, provider: Provider) -> str:
        """Return the key for provider; raises EnvironmentError when unset."""
        key = {
            Provider.OPENAI:   self.openai,
            Provider.DEEPSEEK: self.deepseek,
            Provider.GROQ:     self.groq,
        }[provider]
        if not key:
            raise EnvironmentError(
                f"API key for provider '{provider.value}' is not set. "
                f"Set {provider.value.upper()}_API_KEY or add to [api_keys] in goat.toml."
            )
        return key
