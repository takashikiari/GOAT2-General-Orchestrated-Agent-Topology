"""
GOAT 2.0 — environment-based configuration.
Primary local config: config/goat.toml. Env vars always override toml values.

MEMORY ACCESS ARCHITECTURE:
===========================
This module defines configuration for GOAT 2.0's tiered memory access model:

SUPERVISOR (Full Access):
    - Can read/write to all three memory tiers:
      * WORKING (Redis) — Session-scoped, TTL-enforced
      * EPISODIC (ChromaDB) — Semantic search, persistent
      * LONG_TERM (Letta) — Core memory blocks, most persistent
    - Controls parallel memory pipeline for Redis during DAG execution
    - Validates and stores results in persistent memory post-execution

DAG AGENTS (Restricted Access):
    - Can ONLY access WORKING memory (Redis) via task.memory_manager
    - CANNOT directly access ChromaDB or Letta
    - Prevents memory pollution from agent-executed operations
    - Working memory is session-scoped with TTL enforcement

PARALLEL MEMORY PIPELINE:
    - Runs concurrently with DAG execution for Redis operations
    - Non-blocking memory read/write during task execution
    - Persistent memory writes happen post-execution via supervisor

TEMPERATURE SETTINGS:
=====================
Configuration for LLM sampling temperatures across agent roles:

    Supervisor: 0.5
        - Reduced from default to minimize hallucination
        - Ensures accurate summaries and reduced false information
        - Critical for reliable task validation and reporting
        - Configured in SupervisorConfig.temperature

    Default Agent: 0.4
        - Balanced creativity/accuracy for most agent roles
        - Used by researcher, coder, planner, etc.
        - Configured in BaseAgent.__init__()

    Critic Agent: 0.3
        - Analytical, consistent reviews
        - Lower temperature for deterministic feedback
        - Configured in CriticAgent.__init__()

    Summarizer: 0.5
        - Matches supervisor temperature for consistency
        - Ensures accurate result synthesis

CONFIGURATION RESOLUTION:
=========================
Values resolved in order:
    1. Environment variable (highest priority)
    2. config/goat.toml file
    3. Hard-coded default (lowest priority)

Example:
    SUPERVISOR_MODEL="gpt-4o"  # Env var overrides toml
    # or in goat.toml:
    # [model]
    # supervisor = "deepseek-chat"

PHASE 4 UPDATE:
===============
Module-level `settings = Settings()` singleton REMOVED.
All code must now use Registry for configuration access.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from config.agent_models import AgentModels
from config.api_keys import APIKeys, PROVIDER_BASE_URLS
from config.model_catalogue import ModelSpec, Provider, MODELS, get_model
from config.toml_loader import load_toml

__all__ = [
    "Settings",
    "AgentModels",
    "APIKeys",
    "PROVIDER_BASE_URLS",
    "ModelSpec",
    "Provider",
    "MODELS",
    "get_model",
]

_toml = load_toml()


def _e(env_var: str, toml_val: str = "", default: str = "") -> str:
    """Resolve: env var → toml value → default. Pure.

    Args:
        env_var: Environment variable name
        toml_val: Value from goat.toml (via TomlConfig)
        default: Hard-coded fallback value

    Returns:
        First non-empty value in resolution order

    Example:
        _e("OPENAI_API_KEY", _toml.api_key("openai"), "")
        # Returns os.environ["OPENAI_API_KEY"] if set,
        # else goat.toml [api_keys].openai, else ""
    """
    return os.environ.get(env_var) or toml_val or default


@dataclass
class LettaConfig:
    """
    Letta long-term memory configuration.

    ACCESS RESTRICTION:
    ===================
    - Supervisor-only access for read/write operations
    - DAG agents cannot directly access Letta memory
    - Prevents memory pollution from agent-executed operations

    CONFIGURATION OPTIONS:
    ======================
    - base_url: Letta server endpoint (default: localhost:8283)
    - api_key: Authentication token (from env or toml)
    - embed_model: Embedding model for semantic search
    - llm_model: LLM model for Letta operations
    - memory_token_limit: Maximum tokens for memory context

    ENVIRONMENT VARIABLES:
    ======================
    - LETTA_BASE_URL
    - LETTA_API_KEY
    - LETTA_EMBED_MODEL
    - LETTA_LLM_MODEL
    - LETTA_MEMORY_TOKEN_LIMIT
    """

    base_url: str = field(
        default_factory=lambda: _e(
            "LETTA_BASE_URL", _toml.memory_str("letta_base_url"), "http://localhost:8283"
        )
    )
    api_key: str = field(
        default_factory=lambda: _e("LETTA_API_KEY", _toml.memory_str("letta_api_key"))
    )
    embed_model: str = field(
        default_factory=lambda: _e(
            "LETTA_EMBED_MODEL",
            _toml.memory_str("letta_embed_model"),
            "openai/text-embedding-ada-002",
        )
    )
    llm_model: str = field(
        default_factory=lambda: _e(
            "LETTA_LLM_MODEL",
            _toml.memory_str("letta_llm_model"),
            "openai/gpt-4o-mini",
        )
    )
    memory_token_limit: int = field(
        default_factory=lambda: int(
            os.environ.get("LETTA_MEMORY_TOKEN_LIMIT")
            or _toml.memory_int("letta_token_limit", 4096)
        )
    )

    @property
    def headers(self) -> dict[str, str]:
        """Return HTTP headers for Letta API requests.

        Returns:
            Dict with Content-Type and Authorization (if api_key set)
        """
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h


@dataclass
class SupervisorConfig:
    """
    Supervisor orchestration configuration.

    TEMPERATURE: 0.5
    ================
    - Reduced from default to minimize hallucination
    - Ensures accurate summaries and reduced false information
    - Critical for reliable task validation and reporting
    - Applied to all supervisor LLM calls (decompose_plan, synthesize_results)

    MEMORY ACCESS:
    ==============
    - Full access to all three tiers (Redis, ChromaDB, Letta)
    - Controls parallel memory pipeline for Redis during DAG execution
    - Validates and stores results in persistent memory post-execution
    - DAG agents restricted to working memory only

    CONFIGURATION OPTIONS:
    ======================
    - model_key: Model specification for supervisor LLM calls
    - max_turns: Maximum conversation turns before termination
    - max_workers: Concurrent task execution limit (semaphore)
    - turn_timeout: Seconds before task timeout
    - verbose: Enable detailed logging
    - temperature: LLM sampling temperature (0.5 for accuracy)

    ENVIRONMENT VARIABLES:
    ======================
    - SUPERVISOR_MODEL
    - SUPERVISOR_MAX_TURNS
    - SUPERVISOR_MAX_WORKERS
    - SUPERVISOR_TURN_TIMEOUT
    - SUPERVISOR_VERBOSE
    """

    model_key: str = field(
        default_factory=lambda: _e(
            "SUPERVISOR_MODEL", _toml.model("supervisor"), "gpt-4o"
        )
    )
    max_turns: int = field(
        default_factory=lambda: int(_e("SUPERVISOR_MAX_TURNS", "", "20"))
    )
    max_workers: int = field(
        default_factory=lambda: int(_e("SUPERVISOR_MAX_WORKERS", "", "5"))
    )
    turn_timeout: int = field(
        default_factory=lambda: int(_e("SUPERVISOR_TURN_TIMEOUT", "", "120"))
    )
    verbose: bool = field(
        default_factory=lambda: _e("SUPERVISOR_VERBOSE", "", "false").lower() == "true"
    )
    temperature: float = 0.5  # Reduced temperature for accuracy

    @property
    def model(self) -> ModelSpec:
        """Return ModelSpec for supervisor LLM calls.

        Returns:
            ModelSpec with provider and model_id for supervisor operations
        """
        return get_model(self.model_key)


@dataclass
class Settings:
    """
    GOAT 2.0 central configuration container.

    MEMORY ACCESS HIERARCHY:
    ========================
    - Supervisor: Full access to Redis, ChromaDB, Letta
    - DAG Agents: Working memory (Redis) only
    - Parallel Pipeline: Redis operations during DAG execution

    TEMPERATURE SETTINGS:
    =====================
    - Supervisor: 0.5 (accurate summaries, reduced hallucination)
    - Default Agent: 0.4 (balanced)
    - Critic: 0.3 (analytical consistency)

    CONFIGURATION SECTIONS:
    =======================
    - api_keys: Provider API credentials
    - letta: Letta long-term memory settings
    - supervisor: Supervisor orchestration settings
    - agents: Per-agent model assignments
    - log_level: Logging verbosity
    - env: Environment (development/production)
    - default_model: Fallback model for agents
    - default_provider: Default provider for model resolution

    VALIDATION:
    ===========
    Call settings.validate() to verify:
    - All required API keys are set
    - Provider names are valid
    - Model keys resolve correctly

    PHASE 4 UPDATE:
    ===============
    Module-level `settings = Settings()` singleton REMOVED.
    Instantiate Settings via Registry or directly as needed.
    """

    api_keys: APIKeys = field(default_factory=APIKeys)
    letta: LettaConfig = field(default_factory=LettaConfig)
    supervisor: SupervisorConfig = field(default_factory=SupervisorConfig)
    agents: AgentModels = field(default_factory=AgentModels)
    log_level: str = field(
        default_factory=lambda: _e("LOG_LEVEL", "", "INFO")
    )
    env: str = field(default_factory=lambda: _e("ENV", "", "development"))
    default_model: str = field(
        default_factory=lambda: _e("DEFAULT_MODEL", _toml.model("default"))
    )
    default_provider: str = field(
        default_factory=lambda: _e("DEFAULT_PROVIDER", _toml.model("provider"))
    )

    @property
    def is_production(self) -> bool:
        """Return True if running in production environment.

        Returns:
            True if env == "production", False otherwise
        """
        return self.env.lower() == "production"

    def validate(self) -> None:
        """Raise EnvironmentError for any missing required credentials.

        VALIDATION CHECKS:
        ==================
        - All agent roles have valid model assignments
        - Supervisor model is configured
        - API keys set for all required providers
        - Default provider is a known Provider enum value

        Raises:
            EnvironmentError: If any validation check fails, with detailed
                             error messages for each failure
        """
        _ROLES = (
            "planner",
            "researcher",
            "coder",
            "critic",
            "summarizer",
            "tool_caller",
            "memory",
        )
        providers: set[Provider] = {
            self.agents.get(r).provider for r in _ROLES
        }
        providers.add(self.supervisor.model.provider)
        key_map = {
            Provider.OPENAI: self.api_keys.openai,
            Provider.DEEPSEEK: self.api_keys.deepseek,
            Provider.GROQ: self.api_keys.groq,
        }
        errors = [
            f"  {p.value.upper()}_API_KEY is not set"
            for p in providers
            if not key_map[p]
        ]
        if self.default_provider and self.default_provider not in {
            p.value for p in Provider
        }:
            errors.append(
                f"  DEFAULT_PROVIDER='{self.default_provider}' unknown; "
                f"valid: {[p.value for p in Provider]}"
            )
        if errors:
            raise EnvironmentError(
                "GOAT 2.0 configuration errors:\n" + "\n".join(errors)
            )
