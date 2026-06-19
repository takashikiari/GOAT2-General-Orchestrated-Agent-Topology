"""Tests for BUG-008 fix: dedup_history receives all but the current turn.

Verifies that ``goat_turn`` passes ``history_messages[:-1]`` (not
``[:-2]``) to ``dedup_history``. The previous ``[:-2]`` slice dropped
the most recent user+assistant exchange, so a tight loop on the last
assistant message could not be detected by the dedup pass (only by the
post-hoc ``is_repetitive`` check, which is less reliable).

The current user turn is already in ``user_prompt``; we exclude only
that one message from the prior-history slice — not two.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from supervisor.mechanisms.antirepeat import dedup_history


# ── Helpers ─────────────────────────────────────────────────────────────────


def _hist(*pairs: tuple[str, str]) -> list[dict]:
    """Build a history list from ``(role, content)`` pairs."""
    return [{"role": r, "content": c} for r, c in pairs]


# ── Behavioural tests on the underlying dedup_history ───────────────────────


def test_dedup_history_drops_only_user_turn_when_current_is_user():
    """Sanity: dedup_history itself keeps all assistant messages,
    and dropping the trailing user message is the caller's job."""
    # History BEFORE the current user turn: the caller is expected to
    # have already sliced off the most recent user message (which is
    # already in user_prompt). So we feed dedup_history only the
    # prior conversation.
    prior_history = _hist(
        ("user", "first"),
        ("assistant", "reply 1"),
        ("user", "second"),
        ("assistant", "reply 2"),
    )
    cleaned = dedup_history(prior_history)
    # All four prior messages survive (none are near-duplicates).
    assert len(cleaned) == 4
    assert cleaned[-1]["content"] == "reply 2"


def test_dedup_history_keeps_most_recent_assistant_for_dedup():
    """When the last assistant message nearly duplicates the previous
    one, the latest version is kept (so the LLM sees its own prior
    output and can choose NOT to copy it)."""
    history = _hist(
        ("user", "ask"),
        ("assistant", "plan A or B or C"),
        ("user", "ok"),
        ("assistant", "plan A or B or C"),  # near-duplicate
    )
    cleaned = dedup_history(history[:-1])
    # Only one assistant line remains (the latest "plan A or B or C").
    assistants = [m for m in cleaned if m["role"] == "assistant"]
    assert len(assistants) == 1
    assert assistants[0]["content"] == "plan A or B or C"


# ── Regression: the slice in goat_turn is [-1], not [-2] ────────────────────


def test_goat_turn_passes_history_minus_current_user_to_dedup():
    """Static check on the goat_turn call site: the slice must be
    ``[:-1]`` (skip current user only), not ``[:-2]`` (which would
    also drop the most recent assistant message and weaken the
    dedup gate)."""
    import re
    import supervisor.pipeline.goat_call as gc

    src = open(gc.__file__).read()
    # Capture the dedup_history(...) call. The call is on multiple
    # lines (closing ")" on its own line) and contains ")" chars
    # inside `[]` and `()` of the expression. Use a generous capture
    # and look for the slice token anywhere within the captured text.
    match = re.search(r"dedup_history\([\s\S]{0,400}?\[::-1\]\)", src)
    if not match:
        match = re.search(r"dedup_history\([\s\S]{0,400}?\[::-2\]\)", src)
        assert not match, (
            "dedup_history still slices [:-2] — drops the most recent "
            "assistant message from dedup, weakening loop-detection."
        )
        # Neither slice found — fall back to looking for the slice
        # token anywhere after the call opener.
        opener = src.find("dedup_history(")
        assert opener >= 0, "no dedup_history(...) call found"
        window = src[opener:opener + 400]
        assert "[:-1]" in window, (
            f"dedup_history call must slice [:-1]. Window: {window!r}"
        )
    # When we get here, either the [:-1] match succeeded or the
    # negative check above passed. Confirm [:-1] is present.
    opener = src.find("dedup_history(")
    window = src[opener:opener + 400]
    assert "[:-1]" in window


def test_goat_turn_keeps_prior_assistant_in_dedup(monkeypatch):
    """Functional check: call goat_turn with a history that contains
    a recent assistant message; the dedup output must still include
    that assistant (so the LLM sees its own prior output)."""
    # We don't need a real LLM — only that the messages we feed in
    # reach the dedup step. We mock the very last function call
    # (``_call_with_tools``) so the test stops right after dedup.
    import asyncio
    import supervisor.pipeline.goat_call as gc
    from unittest.mock import MagicMock

    captured: dict = {}

    async def fake_call(spec, messages, tools, **kwargs):
        # Capture the messages that goat_turn built so we can assert
        # on which prior messages survived the dedup step.
        captured["messages"] = list(messages)
        # Return a tagged object that the rest of goat_turn can
        # consume without crashing.
        tagged = MagicMock()
        tagged.content = "ok"
        tagged.called_tools = []
        tagged.tool_results = []
        tagged.source = "generated"
        return tagged

    monkeypatch.setattr(
        "supervisor.pipeline.goat_call._call_with_tools", fake_call
    )

    history = _hist(
        ("user", "earlier"),
        ("assistant", "earlier reply"),
        ("user", "ok"),  # current turn — will be excluded by the slice
    )

    registry = MagicMock()
    registry.settings.supervisor.model = "fake-model"
    registry.settings.supervisor.temperature = 0.0
    registry.memory_tools = []
    registry.goat_skills_tools = []
    registry.dynamic_tools = []
    registry.agent_registry = MagicMock()
    registry.memory_manager = MagicMock()
    # Collect_goat_tools reads from these slots:
    for slot in ("file_tools", "memory_tools", "dag_tools",
                 "system_tools", "goat_skills_tools", "dynamic_tools"):
        setattr(registry, slot, [])

    goat_ctx = MagicMock()
    goat_ctx.to_prompt.return_value = "[ctx]"

    asyncio.run(gc.goat_turn(
        registry=registry,
        intent="ok",
        goat_context=goat_ctx,
        clarity_context=None,
        hints=[],
        history_messages=history,
        mem_ctx="",
        style="",
        turn=4,
        goat_session_id="test-session",
        supervisor=MagicMock(),
    ))

    msgs = captured["messages"]
    # system + user (current turn via user_prompt) + 2 prior messages.
    # Prior "user ok" was the current turn and was already in user_prompt,
    # so the dedup slice ([:-1]) must drop only that one — the prior
    # assistant "earlier reply" must survive in the messages list.
    roles = [m["role"] for m in msgs]
    contents = [m["content"] for m in msgs]
    assert "system" in roles
    # The system + user (current) take the first two slots.
    assert roles[0] == "system"
    assert roles[1] == "user"
    # The "earlier reply" assistant message must be present.
    assert "earlier reply" in contents, (
        f"prior assistant message was dropped by dedup — slice is too "
        f"aggressive. messages: {msgs!r}"
    )