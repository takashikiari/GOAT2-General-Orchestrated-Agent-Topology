"""Model catalogue — Provider, ModelSpec with capability flags, MODELS registry, get_model."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Final

log = logging.getLogger("goat2.config.model_catalogue")

__all__ = ["Provider", "ModelSpec", "MODELS", "get_model"]


class Provider(str, Enum):
    """LLM provider — used to look up API keys and base URLs."""
    OPENAI   = "openai"
    DEEPSEEK = "deepseek"
    GROQ     = "groq"


@dataclass(frozen=True)
class ModelSpec:
    """Immutable model descriptor. tool_calling=False suppresses the tools parameter."""
    provider:        Provider
    model_id:        str
    tool_calling:    bool = True   # False for models that reject the OpenAI tools param
    no_temperature:  bool = False  # True for o-series and gpt-5.5 — omit temperature entirely

    def __str__(self) -> str:
        return f"{self.provider.value}/{self.model_id}"


MODELS: Final[dict[str, ModelSpec]] = {
    # OpenAI
    "gpt-4o":         ModelSpec(Provider.OPENAI,   "gpt-4o"),
    "gpt-4o-mini":    ModelSpec(Provider.OPENAI,   "gpt-4o-mini"),
    "gpt-4-turbo":    ModelSpec(Provider.OPENAI,   "gpt-4-turbo"),
    "gpt-5.5":        ModelSpec(Provider.OPENAI, "gpt-5.5", tool_calling=True, no_temperature=True),
    # DeepSeek
    "deepseek-chat":  ModelSpec(Provider.DEEPSEEK, "deepseek-chat"),
    "deepseek-coder": ModelSpec(Provider.DEEPSEEK, "deepseek-coder"),
    "deepseek-r1":    ModelSpec(Provider.DEEPSEEK, "deepseek-reasoner", tool_calling=False),
    # Groq
    "llama-3.3-70b":  ModelSpec(Provider.GROQ,     "llama-3.3-70b-versatile"),
    "llama-3.1-8b":   ModelSpec(Provider.GROQ,     "llama-3.1-8b-instant"),
    "mixtral-8x7b":   ModelSpec(Provider.GROQ,     "mixtral-8x7b-32768"),
    "gemma2-9b":      ModelSpec(Provider.GROQ,     "gemma2-9b-it"),
}


def get_model(key: str) -> ModelSpec:
    """Look up a ModelSpec by catalogue key; raises ValueError on unknown keys. Pure."""
    if key not in MODELS:
        log.debug("model_catalogue.get_model: unknown key=%r available=%s", key, list(MODELS))
        raise ValueError(f"Unknown model key '{key}'. Available: {list(MODELS)}")
    return MODELS[key]
