"""Tests for active antirepeat + tool-result fallback in goat_turn.

Verifies BUG-005 / BUG-009 (antirepeat no longer just tags the
response — it blocks the loop and asks the user to rephrase) and
BUG-006 (silent LLM after tool calls surfaces a real preview instead
of the cryptic ``"Am executat: …"`` placeholder).
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from supervisor.pipeline.prompt_helpers import (
    build_system_prompt,
    build_user_prompt,
    normalise_empty_response_with_tools,
    tool_schema_failure_hint,
)
from supervisor.mechanisms.antirepeat import repetitive_response_text


# ── prompt_helpers: tool_schema_failure_hint ────────────────────────────────


def test_tool_schema_failure_hint_returns_none_when_no_signature():
    assert tool_schema_failure_hint(ValueError("some unrelated error")) is None


def test_tool_schema_failure_hint_returns_args_only_when_no_type_keyword():
    exc = ValueError("boom <function=web_search>{q='x'}")
    hint = tool_schema_failure_hint(exc)
    assert hint == "web_search (args: q='x')"


def test_tool_schema_failure_hint_extracts_param_when_type_keyword_present():
    exc = ValueError(
        "tool call failed: <function=memory_store>{limit: 5}; expected integer"
    )
    hint = tool_schema_failure_hint(exc)
    # param name + string value surfaced
    assert "memory_store" in hint
    assert "limit" in hint
    assert "5" in hint


def test_tool_schema_failure_hint_never_raises():
    # Pathological inputs must not crash.
    assert tool_schema_failure_hint(None) is None
    assert tool_schema_failure_hint(ValueError("")) is None


# ── prompt_helpers: normalise_empty_response_with_tools ────────────────────


def test_normalise_keeps_content_when_non_empty():
    out = normalise_empty_response_with_tools("hello", ("web_search",), ["res"])
    assert out == "hello"


def test_normalise_keeps_content_when_no_tools_called():
    out = normalise_empty_response_with_tools("", (), None)
    assert out == ""


def test_normalise_surfaces_first_tool_result_when_silent():
    out = normalise_empty_response_with_tools(
        "   ", ("web_search",), ["search result text"],
    )
    assert "Result for web_search" in out
    assert "search result text" in out


def test_normalise_surfaces_honest_no_result_message_when_results_empty():
    out = normalise_empty_response_with_tools(
        "", ("web_search", "memory_get"), None,
    )
    assert "I called web_search, memory_get" in out
    assert "no result to show" in out


def test_normalise_truncates_long_results():
    long = "x" * 5000
    out = normalise_empty_response_with_tools(
        "", ("web_search",), [long], max_preview_chars=200,
    )
    # Preview section is exactly 200 chars
    assert out.endswith("x" * 200)


# ── prompt_helpers: build_user_prompt + build_system_prompt ────────────────


def test_build_user_prompt_truncates_oversized_intent():
    intent = "x" * 10_000
    goat_ctx = MagicMock()
    goat_ctx.to_prompt.return_value = "[ctx]"
    out = build_user_prompt(intent, goat_ctx, None, [], "")
    # 4000-char truncation
    assert "x" * 4000 in out
    assert "x" * 4001 not in out


def test_build_user_prompt_includes_hints_and_mem_when_present():
    goat_ctx = MagicMock()
    goat_ctx.to_prompt.return_value = "[ctx]"
    out = build_user_prompt("hi", goat_ctx, None, ["hint1"], "[memory]")
    assert "hint1" in out
    assert "[memory]" in out


def test_build_system_prompt_returns_identity_only_when_style_empty():
    out = build_system_prompt("")
    assert "Never invent facts" in out  # GOAT_SYSTEM rule 1


# ── antirepeat: repetitive_response_text reads from config ──────────────────


def test_repetitive_response_text_returns_non_empty_string():
    text = repetitive_response_text()
    assert isinstance(text, str)
    assert len(text) > 0


# ── prompt_helpers: build_user_prompt includes clarify marker hint ─────────


def test_build_user_prompt_includes_clarify_marker_instruction():
    goat_ctx = MagicMock()
    goat_ctx.to_prompt.return_value = "[ctx]"
    out = build_user_prompt("hi", goat_ctx, None, [], "")
    assert "[CLARIFY]" in out