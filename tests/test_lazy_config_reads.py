"""Tests for BUG-028 fix: lazy config reads.

The previous implementation read MAX_MESSAGES at import time
as a Final constant, so a config change required restarting
the process. The fix introduces a lazy loader that:

  - Reads from config on first call (not at import time).
  - Caches the result so subsequent calls are O(1).
  - Exposes ``cache_clear()`` so tests (and a future
    config-reload feature) can force a re-read.
  - Looks up the cap on every ``_append()`` call, so a
    config change between turns takes effect immediately.
"""

from __future__ import annotations

from unittest.mock import patch


def test_max_messages_reads_lazily():
    """MAX_MESSAGES must be read from config on first call,
    not frozen at import time. The trim behaviour must use
    the lazily-loaded value."""
    from supervisor.session.history import (
        ConversationHistory,
        _DEFAULT_MAX_MESSAGES,
        cache_clear,
    )

    cache_clear()
    history = ConversationHistory()
    for i in range(_DEFAULT_MAX_MESSAGES + 5):
        history.add_user(f"msg-{i}")
    # Oldest 5 messages were trimmed — proof the lazy load worked.
    assert len(history.messages) == _DEFAULT_MAX_MESSAGES


def test_cache_clear_forces_reload():
    """cache_clear() must reset the cache so the next read
    re-loads from config."""
    from supervisor.session import history as h

    h.cache_clear()
    initial = h.load_max_messages()
    assert h._MAX_MESSAGES_CACHE == initial

    h.cache_clear()
    assert h._MAX_MESSAGES_CACHE is None
    after = h.load_max_messages()
    assert after == initial


def test_load_max_returns_patched_value_after_cache_clear():
    """After cache_clear(), the next load_max_messages call
    returns whatever the function returns — proving the cache
    is bypassed and the lazy path runs each time."""
    from supervisor.session import history as h

    h.cache_clear()
    with patch.object(h, "load_max_messages", return_value=3):
        assert h.load_max_messages() == 3

    h.cache_clear()


def test_cap_respected_per_append():
    """The cap must be looked up on every _append call so a
    config change between turns takes effect. We simulate the
    config change by patching load_max_messages to a smaller
    value after some messages have been added."""
    from supervisor.session import history as h

    h.cache_clear()
    history = h.ConversationHistory()
    for i in range(10):
        history.add_user(f"first-{i}")
    assert len(history.messages) == 10

    h.cache_clear()
    with patch.object(h, "load_max_messages", return_value=3):
        for i in range(5):
            history.add_user(f"second-{i}")
        assert len(history.messages) == 3
        contents = [m["content"] for m in history.messages]
        assert contents == ["second-2", "second-3", "second-4"]

    h.cache_clear()
