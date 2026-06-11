"""Onboarding system configuration constants.

Central registry for onboarding-related constants.
"""
from __future__ import annotations

import logging

log = logging.getLogger("goat2.config.onboarding")

__all__ = [
    "GOAT_VERSION",
    "PROFILE_TTL_WORKING",
    "PROFILE_TTL_SESSION",
    "CHROMA_COLLECTION_NAME",
    "REDIS_KEY_PREFIX",
    "REDIS_KEY_IDENTITY",
    "REDIS_KEY_ONBOARDING",
    "REDIS_KEY_SESSION",
    "CONFIG_REQUIRED_SECTIONS",
    "PIP_TIMEOUT_SECONDS",
    "ENV_FILE_NAME",
    "CONFIG_FILE_NAME",
]

# Version
GOAT_VERSION: str = "2.0"
"""Current GOAT version."""

# TTL values (in seconds)
PROFILE_TTL_WORKING: int = 86400
"""TTL for identity profile in working memory (24 hours)."""

PROFILE_TTL_SESSION: int = 3600
"""TTL for session profile in working memory (1 hour)."""

# Redis key prefixes
REDIS_KEY_PREFIX: str = "goat"
"""Prefix for all GOAT Redis keys."""

REDIS_KEY_IDENTITY: str = "goat:identity:profile"
"""Key for identity profile in working memory."""

REDIS_KEY_ONBOARDING: str = "goat:onboarding:complete"
"""Key for onboarding completion flag."""

REDIS_KEY_SESSION: str = "goat:session:profile"
"""Key for session profile."""

# ChromaDB
CHROMA_COLLECTION_NAME: str = "goat_onboarding"
"""Collection name for onboarding profiles in ChromaDB."""

# Config validation
CONFIG_REQUIRED_SECTIONS: list[str] = ["model", "agents", "memory", "supervisor"]
"""Required sections in goat.toml."""

# Timeouts
PIP_TIMEOUT_SECONDS: int = 120
"""Timeout for pip install in seconds."""

# File names
ENV_FILE_NAME: str = ".env"
"""Name of environment file."""

CONFIG_FILE_NAME: str = "config/goat.toml"
"""Name of GOAT configuration file."""