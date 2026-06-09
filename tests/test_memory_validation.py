"""Tests for memory validation and garbage protection.

Verifies that memory/validation.py correctly:
- Rejects malformed keys and values
- Enforces size limits
- Sanitizes dangerous characters
- Protects Letta from garbage data

Run with: pytest tests/test_memory_validation.py -v
"""
from __future__ import annotations

import pytest

from memory.validation import (
    MAX_KEY_LENGTH,
    MAX_LETTA_BLOCK_LENGTH,
    MAX_VALUE_LENGTH,
    MemoryValidationError,
    sanitize_content,
    validate_key,
    validate_memory_write,
    validate_value,
)


class TestValidateKey:
    """Test key validation logic."""

    def test_valid_key_passes(self) -> None:
        """Valid keys should pass validation."""
        validate_key("test-key")
        validate_key("user/session/data")
        validate_key("key_123")

    def test_empty_key_rejected(self) -> None:
        """Empty keys should be rejected."""
        with pytest.raises(MemoryValidationError, match="empty"):
            validate_key("")
        with pytest.raises(MemoryValidationError, match="empty"):
            validate_key("   ")

    def test_long_key_rejected(self) -> None:
        """Keys exceeding MAX_KEY_LENGTH should be rejected."""
        long_key = "x" * (MAX_KEY_LENGTH + 1)
        with pytest.raises(MemoryValidationError, match="exceeds maximum"):
            validate_key(long_key)

    def test_invalid_chars_rejected(self) -> None:
        """Keys with invalid characters should be rejected."""
        with pytest.raises(MemoryValidationError, match="invalid characters"):
            validate_key("key@invalid")
        with pytest.raises(MemoryValidationError, match="invalid characters"):
            validate_key("key#hash")


class TestValidateValue:
    """Test value validation logic."""

    def test_valid_value_passes(self) -> None:
        """Valid values should pass validation."""
        validate_value("test content")
        validate_value("unicode: 日本語")

    def test_empty_value_rejected(self) -> None:
        """Empty values should be rejected."""
        with pytest.raises(MemoryValidationError, match="empty"):
            validate_value("")
        with pytest.raises(MemoryValidationError, match="empty"):
            validate_value("   ")

    def test_non_string_rejected(self) -> None:
        """Non-string values should be rejected."""
        with pytest.raises(MemoryValidationError, match="must be a string"):
            validate_value(123)  # type: ignore
        with pytest.raises(MemoryValidationError, match="must be a string"):
            validate_value(None)  # type: ignore

    def test_large_value_rejected(self) -> None:
        """Values exceeding limits should be rejected."""
        large_value = "x" * (MAX_VALUE_LENGTH + 1)
        with pytest.raises(MemoryValidationError, match="exceeds maximum"):
            validate_value(large_value)

    def test_letta_stricter_limit(self) -> None:
        """Letta values have stricter size limits."""
        medium_value = "x" * (MAX_LETTA_BLOCK_LENGTH + 1)
        with pytest.raises(MemoryValidationError):
            validate_value(medium_value, tier="long_term")


class TestSanitizeContent:
    """Test content sanitization."""

    def test_null_bytes_removed(self) -> None:
        """Null bytes should be removed."""
        result = sanitize_content("hello\x00world")
        assert result == "helloworld"

    def test_control_chars_removed(self) -> None:
        """Control characters should be removed."""
        result = sanitize_content("test\x01\x02\x03data")
        assert result == "testdata"

    def test_newlines_preserved(self) -> None:
        """Newlines and tabs should be preserved."""
        result = sanitize_content("line1\nline2\ttab\rreturn")
        assert result == "line1\nline2\ttab\rreturn"

    def test_unicode_preserved(self) -> None:
        """Unicode characters should be preserved."""
        result = sanitize_content("日本語 中文 한국어")
        assert result == "日本語 中文 한국어"


class TestValidateMemoryWrite:
    """Test combined validation."""

    def test_valid_write_passes(self) -> None:
        """Valid writes should pass all checks."""
        validate_memory_write("test-key", "test value", tier="working")
        validate_memory_write("user/data", "content", tier="episodic")

    def test_letta_validation_stricter(self) -> None:
        """Letta writes have stricter validation."""
        large_value = "x" * (MAX_LETTA_BLOCK_LENGTH + 1)
        with pytest.raises(MemoryValidationError):
            validate_memory_write(
                "key", large_value, tier="long_term", for_letta=True
            )

    def test_invalid_key_fails(self) -> None:
        """Invalid keys should fail validation."""
        with pytest.raises(MemoryValidationError):
            validate_memory_write("", "value")

    def test_invalid_value_fails(self) -> None:
        """Invalid values should fail validation."""
        with pytest.raises(MemoryValidationError):
            validate_memory_write("key", "")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
