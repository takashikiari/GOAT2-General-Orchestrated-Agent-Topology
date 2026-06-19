"""Tests for error-fallback visibility in GoatSupervisor (BUG-007).

Verifies that when the supervisor's main ``run`` loop catches an
unhandled exception, the ``_empty_result`` fallback surfaces the real
error type + message (instead of swallowing it behind a generic
"could you provide more details?" prompt).

The kernel must always respond — that's the rule that motivates the
try/except in ``run``. But responding honestly means the user / MCP
diagnose_turn can tell what went wrong without spelunking through
debug logs.
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock

from config.registry import ServiceRegistry
from supervisor.supervisor import GoatSupervisor


def _make_supervisor() -> GoatSupervisor:
    registry = ServiceRegistry()
    return GoatSupervisor(registry=registry)


def test_empty_result_includes_error_type_and_message():
    """The fallback summary must include both the exception class
    name and the message so the user can see what broke."""
    sv = _make_supervisor()
    err = ValueError("model endpoint unreachable")
    result = sv._empty_result("test intent", 0.0, err)
    assert "ValueError" in result.summary
    assert "model endpoint unreachable" in result.summary


def test_empty_result_truncates_very_long_error_message():
    """If the error message is huge (e.g. a 10k-char traceback body),
    the fallback must cap it so the prompt doesn't explode."""
    sv = _make_supervisor()
    huge = "x" * 10_000
    err = RuntimeError(huge)
    result = sv._empty_result("test", 0.0, err)
    # The cap is read from [errors] max_chars (default 200 chars).
    # The summary should not contain all 10k chars.
    assert len(result.summary) < 1000


def test_empty_result_logs_at_warning_level(caplog):
    """The fallback must log at WARNING (not DEBUG) so operators
    notice recurring failures without grepping."""
    sv = _make_supervisor()
    err = OSError("connection refused")
    with caplog.at_level(logging.WARNING, logger="goat2.supervisor"):
        sv._empty_result("test", 0.0, err)
    # There should be at least one WARNING-level record mentioning the error.
    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("OSError" in r.getMessage() for r in warning_records)


def test_empty_result_source_is_error():
    """The SupervisorResult.source must be ``"error"`` (not
    ``"generated"``) so downstream channels can render error
    replies distinctly from normal chat replies."""
    sv = _make_supervisor()
    err = KeyError("missing")
    result = sv._empty_result("test", 0.0, err)
    assert result.sources.get("conv") == "error"