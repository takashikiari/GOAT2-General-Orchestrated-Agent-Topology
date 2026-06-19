"""Tests for tool argument coercion and the supervisor's tool-failure hint.

Covers the two patches that stabilise the supervisor+memory boundary:

  1. ``_prepare_args`` in ``tools/tool_runner.py`` — tolerant scalar
     type coercion so models emitting ``"50"`` (string) for an
     integer-typed parameter don't trigger OpenAI 400 invalid_request.
  2. ``_tool_schema_failure_hint`` in
     ``supervisor/pipeline/goat_call.py`` — extracts a one-line
     ``tool.param`` diagnostic from OpenAI tool-use error messages,
     so MCP ``diagnose_turn`` and logs can pinpoint the offending
     tool+param without leaking the full LLM transcript.

These are pure-function tests, no LLM, no Redis, no async.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from supervisor.pipeline.goat_call import _classify_response
from supervisor.pipeline.prompt_helpers import tool_schema_failure_hint as _tool_schema_failure_hint
from tools.tool_runner import _prepare_args


def _schema(props: dict, required: list[str] | None = None) -> MagicMock:
    """Build a fake ToolDefinition with the given JSON-Schema properties."""
    td = MagicMock()
    td.parameters = {
        "type": "object",
        "required": required or [],
        "properties": props,
    }
    return td


# --- _prepare_args: coercion ---


def test_string_to_integer_coercion() -> None:
    sm = {"memory_recent": _schema({"limit": {"type": "integer", "default": 50}})}
    args, err = _prepare_args({"limit": "100"}, sm, "memory_recent")
    assert args == {"limit": 100}
    assert err is None


def test_string_to_number_coercion() -> None:
    sm = {"x": _schema({"temp": {"type": "number"}})}
    args, err = _prepare_args({"temp": "0.7"}, sm, "x")
    assert args == {"temp": 0.7}
    assert err is None


def test_string_to_boolean_coercion() -> None:
    sm = {"x": _schema({"flag": {"type": "boolean"}})}
    args, _ = _prepare_args({"flag": "true"}, sm, "x")
    assert args == {"flag": True}
    args, _ = _prepare_args({"flag": "False"}, sm, "x")
    assert args == {"flag": False}


def test_uncoercible_string_returns_error() -> None:
    sm = {"memory_recent": _schema({"limit": {"type": "integer", "default": 50}})}
    args, err = _prepare_args({"limit": "abc"}, sm, "memory_recent")
    assert err is not None
    assert "integer" in err
    assert "memory_recent.limit" in err


def test_int_passthrough_unchanged() -> None:
    sm = {"x": _schema({"n": {"type": "integer"}})}
    args, err = _prepare_args({"n": 5}, sm, "x")
    assert args == {"n": 5}
    assert err is None


# --- _prepare_args: defaults + required ---


def test_default_applied_when_missing() -> None:
    sm = {"memory_recent": _schema({"limit": {"type": "integer", "default": 50}})}
    args, err = _prepare_args({}, sm, "memory_recent")
    assert args == {"limit": 50}
    assert err is None


def test_required_missing_returns_error() -> None:
    sm = {"memory_search": _schema({"query": {"type": "string"}}, required=["query"])}
    _, err = _prepare_args({}, sm, "memory_search")
    assert err is not None
    assert "query" in err


def test_unknown_tool_returns_error() -> None:
    _, err = _prepare_args({}, {}, "nope")
    assert err is not None
    assert "unknown" in err


# --- _tool_schema_failure_hint ---


def test_hint_extracts_type_mismatch() -> None:
    """The common case: model emitted a string for an integer-typed param."""
    err = Exception(
        'openai.BadRequestError: Error code: 400 - '
        'failed_generation=<function=memory_recent>{"limit": "50"} '
        'errors: [`/limit`: expected integer, but got string]'
    )
    hint = _tool_schema_failure_hint(err)
    assert hint is not None
    assert "memory_recent.limit" in hint
    assert "'50'" in hint


def test_hint_falls_back_to_args_summary() -> None:
    """When the error has a function signature but no type-mismatch keyword."""
    err = Exception('failed_generation=<function=shell>{"cmd": "ls"}')
    hint = _tool_schema_failure_hint(err)
    assert hint is not None
    assert "shell" in hint
    assert "cmd" in hint


def test_hint_returns_none_for_unrelated_errors() -> None:
    assert _tool_schema_failure_hint(Exception("Connection refused")) is None
    assert _tool_schema_failure_hint(ValueError("boom")) is None


# --- _classify_response (regression — unchanged by the patch) ---


def test_classify_clarify_marker() -> None:
    assert _classify_response("hi [CLARIFY]", ()) == ("clarify", "hi")


def test_classify_short_question() -> None:
    assert _classify_response("hi?", ()) == ("clarify", "hi?")


def test_classify_dag() -> None:
    assert _classify_response("go", ("start_dag",)) == ("dag", "go")


def test_classify_direct() -> None:
    assert _classify_response("normal text", ()) == ("direct", "normal text")
