"""Outbound content filter for GOAT 2.0 — masks sensitive credential values.

Applied to all text before sending to external interfaces (Telegram, etc.).
Two-stage: env-file dump detection first, then credential key-name targeting.
"""
from __future__ import annotations

import re
from typing import Final

__all__ = ["mask_sensitive"]

# ALL_CAPS KEY=value at the start of a line (standard env-file format).
_ENV_LINE_RE: Final[re.Pattern[str]] = re.compile(
    r'^([A-Z][A-Z0-9_]{2,})=(.*)',
    re.MULTILINE,
)

# ALL_CAPS KEY=value anywhere in a line (inline env references).
_INLINE_KEY_RE: Final[re.Pattern[str]] = re.compile(
    r'\b([A-Z][A-Z0-9_]{2,})=(\S+)',
)

# KEY: value or KEY=value where the key name signals a credential.
_CRED_RE: Final[re.Pattern[str]] = re.compile(
    r'(?i)(\b\w*(?:api[_\-]?key|secret|password|token|credential|private[_\-]?key)\w*)'
    r'(\s*[=:]\s*)'
    r'(\S+)',
)

_MASK: Final[str] = "****"

# Sensitive path fragments — trigger env-dump masking when found in text.
_SENSITIVE_PATHS: Final[tuple[str, ...]] = (
    ".env", "api_keys", "secrets", "credentials", ".pem", "id_rsa",
)


def _looks_like_env_dump(text: str) -> bool:
    """Return True when text contains ≥2 ALL_CAPS_KEY=value lines."""
    return len(_ENV_LINE_RE.findall(text)) >= 2


def _contains_sensitive_path(text: str) -> bool:
    """Return True when text mentions a known sensitive file path fragment."""
    lower = text.lower()
    return any(p in lower for p in _SENSITIVE_PATHS)


def _mask_env_values(text: str) -> str:
    """Replace ALL_CAPS KEY=value pairs (line-start and inline) with KEY=****."""
    text = _ENV_LINE_RE.sub(lambda m: f"{m.group(1)}={_MASK}", text)
    return _INLINE_KEY_RE.sub(lambda m: f"{m.group(1)}={_MASK}", text)


def mask_sensitive(text: str) -> str:
    """Mask credential values in outbound text before sending to Telegram.

    Stage 1 — env-file dump: if ≥2 KEY=VALUE lines or a sensitive path is
    referenced, mask ALL env-style values in the text.
    Stage 2 — credential keys: always mask values adjacent to known credential
    key names (api_key, secret, password, token, credential, private_key).
    Returns sanitised text; idempotent (already-masked values stay masked).
    """
    if _looks_like_env_dump(text) or _contains_sensitive_path(text):
        text = _mask_env_values(text)
    return _CRED_RE.sub(lambda m: m.group(1) + m.group(2) + _MASK, text)
