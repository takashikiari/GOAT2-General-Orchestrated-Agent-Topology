"""Tests for BUG-?: working memory prominence in user prompt.

Observed failure (session 10:57:31, 2026-06-20): GOAT had the
answer to "what did we talk about at 9:44" in its own working
memory, but launched 17 tool calls to search externally anyway.
The fix places the memory block EARLY in the user prompt —
immediately after the user message — and adds a prompt
directive telling GOAT to read memory BEFORE calling tools.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from supervisor.pipeline.prompt_helpers import build_user_prompt


def _goat_ctx():
    ctx = MagicMock()
    ctx.to_prompt.return_value = "[GOAT capabilities]\n...tools..."
    return ctx


def test_memory_block_appears_before_capabilities():
    """The [Memory] block must appear BEFORE the [GOAT capabilities]
    block so GOAT sees what it already knows before being told
    what tools are available to call."""
    user_prompt = build_user_prompt(
        intent="what did we talk about at 9:44?",
        goat_ctx=_goat_ctx(),
        clarity_ctx=None,
        hints=[],
        mem_ctx="[Present]\n[59s ago] turn:5:intent: hello",
    )
    mem_idx = user_prompt.find("[Present]")
    caps_idx = user_prompt.find("[GOAT capabilities]")
    assert mem_idx >= 0 and caps_idx >= 0, "both blocks must be present"
    assert mem_idx < caps_idx, (
        f"memory block must appear BEFORE capabilities block; "
        f"got memory at {mem_idx}, capabilities at {caps_idx}"
    )


def test_memory_block_carries_read_before_tools_directive():
    """A short directive tells GOAT to read memory BEFORE calling
    tools. Without this, GOAT may treat the memory block as
    background context and reach for tools first."""
    user_prompt = build_user_prompt(
        intent="test",
        goat_ctx=_goat_ctx(),
        clarity_ctx=None,
        hints=[],
        mem_ctx="[Present]\n...",
    )
    assert "Read this BEFORE calling any tools" in user_prompt, (
        "prompt directive missing — GOAT may skip memory and "
        "go straight to tools (the observed 10:57 failure mode)."
    )


def test_memory_block_appears_before_corrective_hints():
    """Memory takes priority over the corrective hints section
    so the LLM grounds itself in local context before
    user-style preferences."""
    user_prompt = build_user_prompt(
        intent="test",
        goat_ctx=_goat_ctx(),
        clarity_ctx=None,
        hints=["hint-1", "hint-2"],
        mem_ctx="[Present]\n...",
    )
    mem_idx = user_prompt.find("[Present]")
    hints_idx = user_prompt.find("Past user corrections")
    assert mem_idx < hints_idx, (
        "memory must precede hints — local knowledge is more "
        "foundational than style preferences"
    )


def test_no_memory_section_when_mem_ctx_empty():
    """When mem_ctx is empty, no memory section is rendered
    (no orphan 'Read this BEFORE...' directive either)."""
    user_prompt = build_user_prompt(
        intent="test",
        goat_ctx=_goat_ctx(),
        clarity_ctx=None,
        hints=[],
        mem_ctx="",
    )
    assert "Read this BEFORE calling any tools" not in user_prompt, (
        "directive should not appear when there's no memory block"
    )


def test_user_message_always_first():
    """The user message is always the first line — it anchors
    what the LLM is responding to."""
    user_prompt = build_user_prompt(
        intent="hello there",
        goat_ctx=_goat_ctx(),
        clarity_ctx=None,
        hints=[],
        mem_ctx="[Present]\n...",
    )
    assert user_prompt.startswith("User message: hello there"), (
        f"prompt must start with user message; got: {user_prompt[:80]!r}"
    )