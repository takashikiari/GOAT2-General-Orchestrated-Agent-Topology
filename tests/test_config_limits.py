"""Tests for config.limits — the centralised default values."""
from __future__ import annotations

from config import limits


# ── Constants are exported and well-typed ──────────────────────────────────


def test_all_expected_defaults_exported():
    expected = {
        "DEFAULT_HISTORY_MAX_MESSAGES",
        "DEFAULT_PROMPT_MAX_ENTRIES",
        "DEFAULT_BACKGROUND_DRAIN_TIMEOUT_S",
        "DEFAULT_ERROR_MAX_CHARS",
        "DEFAULT_CORRECTIONS_LIMIT",
        "DEFAULT_TEMPORAL_FRESH_THRESHOLD_S",
        "DEFAULT_TEMPORAL_RECENT_THRESHOLD_S",
        "DEFAULT_TEMPORAL_DAY_THRESHOLD_S",
    }
    for name in expected:
        assert hasattr(limits, name), (
            f"config.limits must export {name}"
        )


def test_numeric_defaults_are_positive():
    """Numeric defaults (int / float) must be positive — a zero
    or negative value would break the mechanism silently.
    String defaults (URLs, log level) are not subject to the
    positivity check."""
    string_defaults = {"SEARXNG_URL", "LOG_LEVEL", "VERBOSITY_DEFAULT"}
    for name in limits.__all__:
        if name in string_defaults:
            continue
        value = getattr(limits, name)
        assert isinstance(value, (int, float)), (
            f"{name} should be numeric, got {type(value).__name__}"
        )
        assert value > 0, f"{name} must be > 0 (got {value})"


def test_history_default_matches_module_local():
    """The canonical DEFAULT_HISTORY_MAX_MESSAGES must match
    the module-local default in supervisor.session.history."""
    from supervisor.session.history import _DEFAULT_MAX_MESSAGES
    assert limits.DEFAULT_HISTORY_MAX_MESSAGES == _DEFAULT_MAX_MESSAGES


def test_prompt_default_matches_module_local():
    """The canonical DEFAULT_PROMPT_MAX_ENTRIES must match
    the module-local default in supervisor.mechanisms.context_builder."""
    from supervisor.mechanisms.context_builder import _DEFAULT_MAX_ENTRIES
    assert limits.DEFAULT_PROMPT_MAX_ENTRIES == _DEFAULT_MAX_ENTRIES


def test_drain_default_matches_module_local():
    """The canonical DEFAULT_BACKGROUND_DRAIN_TIMEOUT_S must match
    the module-local default in supervisor.background_drain."""
    from supervisor.background_drain import DEFAULT_DRAIN_TIMEOUT_S
    assert limits.DEFAULT_BACKGROUND_DRAIN_TIMEOUT_S == DEFAULT_DRAIN_TIMEOUT_S


def test_error_default_matches_module_local():
    """The canonical DEFAULT_ERROR_MAX_CHARS must match the
    module-local default in supervisor.errors_fallback."""
    from supervisor.errors_fallback import _DEFAULT_MAX_CHARS
    assert limits.DEFAULT_ERROR_MAX_CHARS == _DEFAULT_MAX_CHARS


def test_temporal_defaults_match_module_local():
    """The canonical temporal thresholds must match the
    module-local defaults in memory.temporal.temporal_format."""
    from memory.temporal.temporal_format import (
        DEFAULT_FRESH_THRESHOLD_S,
        DEFAULT_RECENT_THRESHOLD_S,
        DEFAULT_DAY_THRESHOLD_S,
    )
    assert limits.DEFAULT_TEMPORAL_FRESH_THRESHOLD_S == DEFAULT_FRESH_THRESHOLD_S
    assert limits.DEFAULT_TEMPORAL_RECENT_THRESHOLD_S == DEFAULT_RECENT_THRESHOLD_S
    assert limits.DEFAULT_TEMPORAL_DAY_THRESHOLD_S == DEFAULT_DAY_THRESHOLD_S


def test_corrections_default_matches_module_local():
    """The canonical DEFAULT_CORRECTIONS_LIMIT must match the
    module-local default in supervisor.mechanisms.corrections."""
    from supervisor.mechanisms.corrections import DEFAULT_LIMIT
    assert limits.DEFAULT_CORRECTIONS_LIMIT == DEFAULT_LIMIT