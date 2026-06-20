"""Tests for utils.logging_policy — the standard logging helpers."""
from __future__ import annotations

import logging

import pytest

from utils.logging_policy import (
    EXPECTED_FAILURE_HINT,
    log_unexpected_failure,
    safe_log_exception,
)


def test_safe_log_exception_at_warning_default(caplog):
    log = logging.getLogger("test_safe_log_default")
    with caplog.at_level(logging.WARNING, logger="test_safe_log_default"):
        safe_log_exception(log, "thing failed", ValueError("boom"))
    records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert records
    assert any("thing failed" in r.getMessage() and "boom" in r.getMessage()
               for r in records)


def test_safe_log_exception_at_debug_when_requested(caplog):
    log = logging.getLogger("test_safe_log_debug")
    with caplog.at_level(logging.DEBUG, logger="test_safe_log_debug"):
        safe_log_exception(
            log, "expected failure", RuntimeError("nope"),
            level=logging.DEBUG,
        )
    # We expect a DEBUG record (or none, if DEBUG is not captured)
    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert any("expected failure" in r.getMessage() for r in debug_records)


def test_safe_log_exception_includes_traceback_at_warning(caplog):
    log = logging.getLogger("test_safe_log_traceback")
    with caplog.at_level(logging.WARNING, logger="test_safe_log_traceback"):
        try:
            raise RuntimeError("crash")
        except RuntimeError as exc:
            safe_log_exception(log, "context", exc)
    records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    # At least one record carries the formatted exception (or
    # the exc_info attribute is set).
    assert any(
        r.exc_info is not None or "crash" in r.getMessage()
        for r in records
    )


def test_log_unexpected_failure_at_warning(caplog):
    log = logging.getLogger("test_log_unexpected")
    with caplog.at_level(logging.WARNING, logger="test_log_unexpected"):
        try:
            raise OSError("connection refused")
        except OSError as exc:
            log_unexpected_failure(log, "during tool call", exc)
    records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(
        "UNEXPECTED" in r.getMessage()
        and "during tool call" in r.getMessage()
        and "OSError" in r.getMessage()
        for r in records
    )


def test_log_unexpected_failure_includes_traceback(caplog):
    log = logging.getLogger("test_log_unexpected_tb")
    with caplog.at_level(logging.WARNING, logger="test_log_unexpected_tb"):
        try:
            raise ValueError("oops")
        except ValueError as exc:
            log_unexpected_failure(log, "in some block", exc)
    records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(r.exc_info is not None for r in records)


def test_expected_failure_hint_constant_exported():
    """The constant is exported so callers can reference it
    in their own docstrings without copy-paste."""
    assert isinstance(EXPECTED_FAILURE_HINT, str)
    assert EXPECTED_FAILURE_HINT  # non-empty
