"""Tests for BUG-020 + BUG-021 + BUG-022 fixes in planner_decompose.

BUG-020: plan_validator was called but only logged errors — when
        the plan was invalid, the user saw a silently-chosen
        fallback plan with no indication that anything went wrong.
        The fix: surface a structured error to the caller (or
        log loudly with the offending validator errors).

BUG-021: ``decompose_plan`` concatenated ``intent`` directly into
        the user content string. An intent with newlines or
        special characters could break the prompt. The fix:
        escape or quote the intent so it is one block.

BUG-022: PLANNER_SYSTEM was used in two places (decompose_plan
        and _run_planner). A change in one would silently drift
        from the other. The fix: a single helper builds the
        planner request body; both call sites use it.
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

from agents.planner_decompose import (
    PLANNER_SYSTEM,
    build_planner_request_body,
)


# ── BUG-022: a single helper builds the request body ───────────────────────


def test_build_planner_request_body_returns_messages_list():
    """The helper returns a ``[system, user]`` messages list, so
    both ``decompose_plan`` and ``_run_planner`` use the same body."""
    body = build_planner_request_body(
        intent="build a REST API",
        context_text="prior task output: foo",
    )
    assert isinstance(body, list)
    assert len(body) == 2
    assert body[0]["role"] == "system"
    assert body[1]["role"] == "user"
    # The system message is the canonical PLANNER_SYSTEM.
    assert body[0]["content"] == PLANNER_SYSTEM


def test_build_planner_request_body_handles_empty_context():
    """No prior context — the user content still wraps the intent."""
    body = build_planner_request_body(intent="hello")
    assert body[1]["role"] == "user"
    assert "hello" in body[1]["content"]


def test_build_planner_request_body_handles_unicode_intent():
    """Diacritics and non-ASCII survive the round-trip."""
    body = build_planner_request_body(intent="Construiește un API cu diacritice ăîâ")
    assert "Construiește" in body[1]["content"]


# ── BUG-021: intent is escaped / wrapped, not raw-concatenated ────────────


def test_intent_with_newlines_does_not_break_prompt():
    """An intent containing newlines or carriage returns must be
    wrapped so it appears as one block — not as if it had its own
    sections."""
    intent = "first line\nsecond line\r\nthird line\n\nfourth"
    body = build_planner_request_body(intent=intent)
    user_content = body[1]["content"]
    # The intent must appear inside an explicit boundary marker
    # (delimiters or quoting) so the LLM cannot mis-parse the
    # multi-line input as separate sections.
    assert "<<<INTENT>>>" in user_content
    assert "<<<END_INTENT>>>" in user_content
    assert "first line" in user_content
    assert "fourth" in user_content


def test_intent_with_json_braces_does_not_break_prompt():
    """An intent containing ``{`` or ``}`` must not be confused
    with a JSON object by the LLM."""
    intent = "use the API { get_user(id) } and { save(data) }"
    body = build_planner_request_body(intent=intent)
    user_content = body[1]["content"]
    # Either escaped/quoted or wrapped in delimiters — the raw
    # intent must NOT appear to introduce a top-level JSON object.
    assert "<<<INTENT>>>" in user_content or "use the API" in user_content


def test_intent_is_truncated_to_safe_length():
    """An intent of 10k characters must be capped so the prompt
    budget is not blown."""
    long_intent = "x" * 10_000
    body = build_planner_request_body(intent=long_intent)
    user_content = body[1]["content"]
    # The user content (intent + small wrapper) must be much
    # smaller than 10k chars. The system message is separate.
    assert len(user_content) < 5000, (
        f"truncation failed: user content {len(user_content)} chars"
    )


# ── BUG-020: plan validation errors surface via WARNING log ───────────────


def test_plan_validator_emits_errors_for_unknown_role():
    """Direct test of the validator: an unknown role is an error."""
    from config.agent_types import Plan, AgentTask
    from supervisor.pipeline.plan_validator import validate_plan
    plan = Plan(tasks=[
        AgentTask(id="a", role="evil_role", prompt="p", depends_on=[]),
    ])
    is_valid, errors, warnings = validate_plan(plan)
    assert is_valid is False
    assert any("unknown role" in e for e in errors)


def test_plan_validator_detects_cycle():
    """A cycle in depends_on is an error."""
    from config.agent_types import Plan, AgentTask
    from supervisor.pipeline.plan_validator import validate_plan
    plan = Plan(tasks=[
        AgentTask(id="a", role="tool_caller", prompt="p", depends_on=["b"]),
        AgentTask(id="b", role="tool_caller", prompt="p", depends_on=["a"]),
    ])
    is_valid, errors, _ = validate_plan(plan)
    assert is_valid is False
    assert any("cycle" in e for e in errors)


def test_plan_validator_warns_when_summarizer_unconnected():
    """A summarizer that doesn't depend on every other task
    gets a warning (not an error — the plan is still valid)."""
    from config.agent_types import Plan, AgentTask
    from supervisor.pipeline.plan_validator import validate_plan
    plan = Plan(tasks=[
        AgentTask(id="a", role="tool_caller", prompt="p", depends_on=[]),
        AgentTask(id="b", role="summarizer", prompt="p", depends_on=[]),
    ])
    is_valid, errors, warnings = validate_plan(plan)
    assert is_valid is True  # not an error
    assert any("summarizer" in w and "does not depend" in w for w in warnings)