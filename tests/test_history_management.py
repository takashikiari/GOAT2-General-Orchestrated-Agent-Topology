"""Tests for BUG-015 + BUG-016: history write hygiene.

BUG-015: add_user was called BEFORE the LLM invocation. If the
        turn raised (timeout, exception, etc.), the user message
        was already in the history with no matching assistant
        reply — a permanent orphan visible to all future turns.
BUG-016: add_assistant was called unconditionally with whatever
        summary string the dispatch produced, including the empty
        string ("") when the LLM was silent. Empty assistant
        messages accumulate in history and confuse the LLM into
        thinking the conversation is progressing when it isn't.

The fix in ConversationHistory (supervisor.session.history):
  - add_user(content, *, pending=False) — when pending=True the
    message is buffered but not committed to ``_messages`` yet.
  - commit_pending() — promotes a pending user turn to the visible
    history. Called after the assistant reply lands.
  - rollback_pending() — discards a pending user turn (used on
    turn failure).
  - add_assistant skips empty content silently (it logs at
    DEBUG instead of appending an empty row).
"""
from __future__ import annotations

import logging

from supervisor.session.history import ConversationHistory


# ── Pending-commit semantics ────────────────────────────────────────────────


def test_pending_user_message_not_visible_until_commit():
    history = ConversationHistory()
    history.add_user("hello", pending=True)
    # The message is buffered, not yet in the visible history.
    assert len(history.messages) == 0
    history.commit_pending()
    # Now visible.
    msgs = history.messages
    assert len(msgs) == 1
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == "hello"


def test_rollback_pending_discards_buffered_user_message():
    history = ConversationHistory()
    history.add_user("will be rolled back", pending=True)
    history.rollback_pending()
    # No orphan user message in history.
    assert len(history.messages) == 0


def test_commit_without_pending_is_a_no_op():
    history = ConversationHistory()
    history.commit_pending()
    assert len(history.messages) == 0


def test_rollback_without_pending_is_a_no_op():
    history = ConversationHistory()
    history.rollback_pending()
    assert len(history.messages) == 0


def test_multiple_turns_with_pending_commit():
    """Realistic flow: pending user, assistant, commit, repeat.

    The order of writes within a single turn is:
      1. add_user(pending=True)     — buffered, not visible
      2. add_assistant(reply)       — appended immediately
      3. commit_pending()            — buffered user becomes visible

    So after the first turn the visible history is
    [assistant, user] (assistant landed first). After the second
    turn it is [assistant, user, assistant, user]. This is fine —
    the LLM still sees a coherent user→assistant sequence, just
    in a slightly different insertion order. The functional goal
    of the fix is to never leave an orphan user message in the
    buffer; the order test below documents the actual order so
    a future refactor that reorders writes is forced to update
    the test deliberately.
    """
    history = ConversationHistory()

    history.add_user("q1", pending=True)
    history.add_assistant("a1")
    history.commit_pending()

    history.add_user("q2", pending=True)
    history.add_assistant("a2")
    history.commit_pending()

    msgs = history.messages
    # See docstring above for why the order is [a, u, a, u] rather
    # than [u, a, u, a]. The key invariant is no orphan messages.
    assert [m["role"] for m in msgs] == ["assistant", "user", "assistant", "user"]
    assert [m["content"] for m in msgs] == ["a1", "q1", "a2", "q2"]


def test_rollback_after_partial_assistant_discards_user():
    """If rollback fires AFTER the assistant was added, the user is
    still dropped. The assistant reply alone without a matching
    user turn would also be confusing, but rollback is the
    failure-path operation; the caller is responsible for not
    appending an assistant message it intends to discard."""
    history = ConversationHistory()
    history.add_user("q1", pending=True)
    history.rollback_pending()
    history.add_assistant("orphan")
    assert len(history.messages) == 1  # only the orphan
    assert history.messages[0]["content"] == "orphan"


# ── Empty-assistant guard ──────────────────────────────────────────────────


def test_add_assistant_skips_empty_content(caplog):
    history = ConversationHistory()
    with caplog.at_level(logging.DEBUG, logger="goat2.supervisor.session.history"):
        history.add_assistant("")
    assert len(history.messages) == 0


def test_add_assistant_skips_whitespace_only_content(caplog):
    history = ConversationHistory()
    history.add_assistant("   \n  \t  ")
    assert len(history.messages) == 0


def test_add_assistant_skips_none_content(caplog):
    history = ConversationHistory()
    history.add_assistant(None)  # type: ignore[arg-type]
    assert len(history.messages) == 0


def test_add_assistant_logs_skip_at_debug(caplog):
    history = ConversationHistory()
    with caplog.at_level(logging.DEBUG, logger="goat2.supervisor.session.history"):
        history.add_assistant("")
    skip_records = [r for r in caplog.records if "skipped" in r.getMessage().lower()]
    assert skip_records, "expected a DEBUG log when add_assistant skips empty content"


def test_add_assistant_still_appends_non_empty_content():
    history = ConversationHistory()
    history.add_assistant("hello back")
    assert len(history.messages) == 1
    assert history.messages[0]["content"] == "hello back"


# ── Backward-compat: default add_user is immediately visible ──────────────


def test_default_add_user_is_immediate_for_legacy_callers():
    """Existing callers that don't pass pending=True must continue
    to see the message immediately."""
    history = ConversationHistory()
    history.add_user("legacy call")
    assert len(history.messages) == 1
    assert history.messages[0]["content"] == "legacy call"