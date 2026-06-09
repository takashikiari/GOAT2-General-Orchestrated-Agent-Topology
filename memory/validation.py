"""Memory validation — input sanitization and garbage protection.

This module provides validation logic to prevent corrupted, malformed,
or excessively large data from being stored in memory systems.

Applies to all three tiers:
- WORKING (Redis): Session-scoped with TTL
- EPISODIC (ChromaDB): Semantic search storage
- LONG_TERM (Letta): Core memory blocks

Validation checks:
- Key format and length limits
- Content size limits
- Content sanitization (remove control characters)
- Type validation
- Empty/whitespace-only rejection

All write operations should call validate_memory_write() before storing.
"""
from __future__ import annotations

import re
from typing import Final

__all__ = [
    "MemoryValidationError",
    "MAX_KEY_LENGTH",
    "MAX_VALUE_LENGTH",
    "MAX_LETTA_BLOCK_LENGTH",
    "validate_key",
    "validate_value",
    "validate_memory_write",
    "sanitize_content",
]

# ---------------------------------------------------------------------------
# Constants — size limits for memory operations
# ---------------------------------------------------------------------------

MAX_KEY_LENGTH: Final[int] = 256
"""Maximum length for memory keys (prevents key bloat)."""

MAX_VALUE_LENGTH: Final[int] = 1_000_000
"""Maximum length for working/episodic memory values (1MB)."""

MAX_LETTA_BLOCK_LENGTH: Final[int] = 100_000
"""Maximum length for Letta core memory blocks (100KB)."""

MIN_CONTENT_LENGTH: Final[int] = 1
"""Minimum non-whitespace content length."""

# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class MemoryValidationError(ValueError):
    """Raised when memory input validation fails.

    Attributes:
        field: The field that failed validation ('key' or 'value').
        reason: Human-readable explanation of the failure.
    """

    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
        super().__init__(f"Memory validation failed for {field}: {reason}")


# ---------------------------------------------------------------------------
# Validation functions
# ---------------------------------------------------------------------------


def validate_key(key: str) -> None:
    """Validate a memory key format.

    Checks:
    - Key is not empty or whitespace-only
    - Key length <= MAX_KEY_LENGTH
    - Key contains only safe characters (alphanumeric, dash, underscore, slash)

    Args:
        key: The memory key to validate.

    Raises:
        MemoryValidationError: If key fails any validation check.
    """
    if not key or not key.strip():
        raise MemoryValidationError("key", "cannot be empty or whitespace")

    if len(key) > MAX_KEY_LENGTH:
        raise MemoryValidationError(
            "key",
            f"exceeds maximum length ({len(key)} > {MAX_KEY_LENGTH})",
        )

    # Allow alphanumeric, dash, underscore, slash, dot
    if not re.match(r"^[\w\-./]+$", key):
        raise MemoryValidationError(
            "key",
            "contains invalid characters (use alphanumeric, dash, underscore, slash, dot)",
        )


def validate_value(value: str, tier: str = "working") -> None:
    """Validate a memory value.

    Checks:
    - Value is not empty or whitespace-only
    - Value length <= tier-specific limit
    - Value is a string

    Args:
        value: The memory value to validate.
        tier: The target tier ('working', 'episodic', 'long_term').

    Raises:
        MemoryValidationError: If value fails any validation check.
    """
    if not isinstance(value, str):
        raise MemoryValidationError("value", "must be a string")

    if not value.strip():
        raise MemoryValidationError("value", "cannot be empty or whitespace-only")

    # Tier-specific limits
    if tier == "long_term":
        max_len = MAX_LETTA_BLOCK_LENGTH
    else:
        max_len = MAX_VALUE_LENGTH

    if len(value) > max_len:
        raise MemoryValidationError(
            "value",
            f"exceeds maximum length for {tier} ({len(value)} > {max_len})",
        )


def sanitize_content(content: str) -> str:
    """Sanitize content by removing dangerous control characters.

    Removes:
    - Null bytes (\x00)
    - Other control characters except newline, tab, carriage return

    Preserves:
    - Normal text
    - Unicode characters
    - Newlines, tabs, carriage returns

    Args:
        content: The content to sanitize.

    Returns:
        Sanitized content string.
    """
    # Remove null bytes and control chars (except \n, \t, \r)
    sanitized = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", content)
    return sanitized


def validate_memory_write(
    key: str,
    value: str,
    tier: str = "working",
    for_letta: bool = False,
) -> None:
    """Validate a complete memory write operation.

    Combines key and value validation with sanitization.

    Args:
        key: The memory key.
        value: The memory value.
        tier: Target tier ('working', 'episodic', 'long_term').
        for_letta: If True, apply stricter Letta-specific validation.

    Raises:
        MemoryValidationError: If any validation check fails.

    Side Effects:
        Sanitizes value in-place (callers should use returned value).
    """
    validate_key(key)

    if for_letta:
        # Stricter validation for Letta core memory
        if len(value) > MAX_LETTA_BLOCK_LENGTH:
            raise MemoryValidationError(
                "value",
                f"exceeds Letta block limit ({len(value)} > {MAX_LETTA_BLOCK_LENGTH})",
            )

    validate_value(value, tier)
