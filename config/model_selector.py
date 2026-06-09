"""Dynamic model selection with fallback priority lists.

Replaces hard-coded model fallbacks (e.g., "gpt-4o") with configurable
priority lists that respect user preferences from goat.toml.

Usage:
    from config.model_selector import get_model_for_role
    spec = get_model_for_role("planner")  # Returns first available model

Configuration (goat.toml):
    [agents.planner]
    models = ["deepseek-r1", "gpt-4o", "llama-3.3-70b"]
    
    Or backward-compatible single model:
    [agents]
    planner = "deepseek-r1"
"""
from __future__ import annotations

import logging
from typing import Final

from config.model_catalogue import ModelSpec, get_model, Provider
from config.api_keys import APIKeys
from config.toml_loader import load_toml

__all__ = ["ModelUnavailableError", "get_model_for_role", "check_model_health"]

log = logging.getLogger("goat2.model_selector")

_toml = load_toml()

# Default fallback chain per role (used if not configured in goat.toml)
_DEFAULT_FALLBACKS: Final[dict[str, list[str]]] = {
    "planner": ["deepseek-r1", "gpt-4o", "llama-3.3-70b"],
    "researcher": ["deepseek-chat", "gpt-4o-mini", "llama-3.3-70b"],
    "coder": ["deepseek-coder", "gpt-4o", "llama-3.3-70b"],
    "critic": ["llama-3.3-70b", "gpt-4o-mini", "deepseek-chat"],
    "summarizer": ["llama-3.1-8b", "gpt-4o-mini", "deepseek-chat"],
    "tool_caller": ["deepseek-chat", "gpt-4o-mini", "llama-3.3-70b"],
    "memory": ["llama-3.1-8b", "gpt-4o-mini", "deepseek-chat"],
}


class ModelUnavailableError(Exception):
    """Raised when no configured model is available for a role."""
    pass


def _get_model_list_for_role(role: str) -> list[str]:
    """Get prioritized model list for a role from config or defaults.

    Checks:
    1. [agents.{role}].models list in goat.toml
    2. [agents].{role} single model in goat.toml
    3. Environment variable AGENT_{ROLE}_MODEL
    4. Default fallback chain

    Args:
        role: Agent role name (e.g., "planner", "coder")

    Returns:
        List of model keys in priority order (first = preferred)
    """
    import os

    # Check env var first (highest priority)
    env_var = f"AGENT_{role.upper()}_MODEL"
    env_model = os.environ.get(env_var)
    if env_model:
        return [env_model]

    # Check goat.toml [agents.{role}].models list
    role_config = _toml._agents.get(role)
    if isinstance(role_config, dict):
        models_list = role_config.get("models", [])
        if models_list:
            return models_list

    # Check goat.toml [agents].{role} single model (backward-compatible)
    single_model = _toml.agent(role)
    if single_model:
        return [single_model]

    # Fall back to default chain
    return _DEFAULT_FALLBACKS.get(role, ["gpt-4o-mini"])


def check_model_health(spec: ModelSpec, api_keys: APIKeys | None = None) -> bool:
    """
    Quick health check for a model (API key presence + basic validation).

    Does NOT make network calls - just validates configuration.
    For actual availability, the LLM call itself will reveal issues.

    Args:
        spec: ModelSpec to check
        api_keys: Optional APIKeys instance. If None, creates from Settings.

    Returns:
        True if model appears configured correctly, False otherwise
    """
    try:
        # Check API key exists for provider
        if api_keys is None:
            from config.settings import Settings
            api_keys = Settings().api_keys
        key = api_keys.for_provider(spec.provider)
        if not key:
            log.debug("Model %s: API key missing for %s", spec.model_id, spec.provider.value)
            return False
        return True
    except EnvironmentError:
        log.debug("Model %s: API key not configured", spec.model_id)
        return False


def get_model_for_role(role: str) -> ModelSpec:
    """Get first available ModelSpec for a role from priority list.

    Iterates through configured models for the role and returns the
    first one that passes health checks. If all models fail, raises
    ModelUnavailableError instead of silently falling back to hard-coded name.

    Args:
        role: Agent role name (e.g., "planner", "coder", "critic")

    Returns:
        ModelSpec for first available model in priority list

    Raises:
        ModelUnavailableError: If no configured models are available
    """
    model_list = _get_model_list_for_role(role)
    last_error: Exception | None = None

    for model_key in model_list:
        try:
            spec = get_model(model_key)
            if check_model_health(spec):
                if model_key != model_list[0]:
                    log.info(
                        "ModelSelector: using fallback %s for role=%s (preferred=%s failed)",
                        model_key, role, model_list[0],
                    )
                return spec
            log.debug("Model %s failed health check for role=%s", model_key, role)
        except ValueError as e:
            log.debug("Model %s not found in catalogue: %s", model_key, e)
            last_error = e
        except Exception as e:
            log.debug("Model %s health check failed: %s", model_key, e)
            last_error = e

    # No models available - raise clear error instead of silent fallback
    error_msg = (
        f"No available models for role '{role}'. "
        f"Checked: {model_list}. "
        f"Last error: {last_error}"
    )
    log.error(error_msg)
    raise ModelUnavailableError(error_msg)
