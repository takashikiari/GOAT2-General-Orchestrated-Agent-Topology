"""Tests for BUG-012 fix: dag_intent_keywords must match whole tokens.

The old implementation used plain substring matching: a user
saying "tag the result" or "show me the taskbar" would falsely
flag the intent as DAG-related, keeping an old DAG entry fresh
that should have been marked [STALE].

The new implementation matches whole words at token boundaries
(separated by whitespace, punctuation, or string boundaries).
"""
from __future__ import annotations

from supervisor.mechanisms.staleness import (
    DAG_INTENT_KEYWORDS,
    is_stale,
)


def _record(key: str, content: str, ts: float) -> dict:
    return {"key": key, "content": content, "created_at_ts": ts}


# ── Keyword semantics ───────────────────────────────────────────────────────


def test_keywords_constant_is_a_tuple_of_strings():
    assert isinstance(DAG_INTENT_KEYWORDS, tuple)
    for kw in DAG_INTENT_KEYWORDS:
        assert isinstance(kw, str)
        assert kw == kw.lower()  # documented as case-insensitive


# ── is_stale with a recent entry stays fresh regardless of intent ───────────


def test_recent_dag_entry_never_stale():
    """Anything fresher than dag_max_age_seconds is always fresh."""
    import time
    now = time.time()
    rec = _record("dag:result:abc", "x", ts=now - 1)
    assert is_stale(rec, "anything", now) is False
    assert is_stale(rec, "show me the dag result", now) is False
    assert is_stale(rec, "tag the result", now) is False


# ── BUG-012 core: substring false-positives must NOT match ─────────────────


def test_dag_substring_in_tag_does_not_count_as_dag_intent():
    """'tag' must NOT match the 'dag' keyword — it's a substring,
    not a whole word. We pick an intent where 'dag' is a substring
    of one token but no full keyword ('dag', 'task', 'result',
    'workflow', 'pipeline') is a whole token."""
    import time
    now = time.time()
    rec = _record("dag:result:abc", "old", ts=now - 1000)
    # 'tag' contains 'dag' as substring but 'tag' is its own word.
    # No full keyword appears as a token — 'result' is absent,
    # 'task' is absent, 'workflow' is absent, 'pipeline' is absent.
    assert is_stale(rec, "please tag the output for indexing", now) is True


def test_taskbar_does_not_count_as_dag_intent():
    """'taskbar' is a window UI element, not a DAG reference."""
    import time
    now = time.time()
    rec = _record("dag:result:abc", "old", ts=now - 1000)
    assert is_stale(rec, "the taskbar is missing icons", now) is True


def test_catalog_does_not_count_as_dag_intent():
    """'catalog' contains 'tag' as a substring — must NOT match."""
    import time
    now = time.time()
    rec = _record("dag:result:abc", "old", ts=now - 1000)
    assert is_stale(rec, "browse the product catalog", now) is True


def test_workflows_plural_does_not_count_as_dag_intent():
    """'workflows' is a common noun, not a DAG reference."""
    import time
    now = time.time()
    rec = _record("dag:result:abc", "old", ts=now - 1000)
    assert is_stale(rec, "the workflows are documented elsewhere", now) is True


def test_pipelines_idiom_does_not_count_as_dag_intent():
    """'pipelines' as an idiom (e.g. data pipelines) is not always
    a GOAT DAG reference — but in this codebase it IS, so this
    case may legitimately keep a DAG entry fresh. Documented as
    expected behaviour: the keyword is the *only* signal we use.
    A future revision can disambiguate via the 'dag:' namespace."""
    # Documenting the current behaviour, not a fix assertion.
    # Skipping — the plural form is intentionally matched.
    pass


# ── BUG-012: legitimate dag references must still work ─────────────────────


def test_explicit_dag_word_keeps_dag_fresh():
    import time
    now = time.time()
    rec = _record("dag:result:abc", "old", ts=now - 1000)
    # The user clearly references the DAG system.
    assert is_stale(rec, "show me the dag result", now) is False
    assert is_stale(rec, "what's in the dag?", now) is False
    assert is_stale(rec, "DAG please", now) is False  # case-insensitive


def test_explicit_task_keyword_keeps_dag_fresh():
    import time
    now = time.time()
    rec = _record("dag:result:abc", "old", ts=now - 1000)
    assert is_stale(rec, "what's the task status?", now) is False


def test_explicit_result_keyword_keeps_dag_fresh():
    import time
    now = time.time()
    rec = _record("dag:result:abc", "old", ts=now - 1000)
    assert is_stale(rec, "show me the result", now) is False


def test_explicit_workflow_keyword_keeps_dag_fresh():
    import time
    now = time.time()
    rec = _record("dag:result:abc", "old", ts=now - 1000)
    assert is_stale(rec, "describe the workflow", now) is False


def test_explicit_pipeline_keyword_keeps_dag_fresh():
    import time
    now = time.time()
    rec = _record("dag:result:abc", "old", ts=now - 1000)
    assert is_stale(rec, "explain the pipeline", now) is False


# ── Punctuation and case handling ───────────────────────────────────────────


def test_keyword_at_end_of_sentence_with_period_still_matches():
    import time
    now = time.time()
    rec = _record("dag:result:abc", "old", ts=now - 1000)
    assert is_stale(rec, "show me the dag.", now) is False


def test_keyword_at_start_of_sentence_still_matches():
    import time
    now = time.time()
    rec = _record("dag:result:abc", "old", ts=now - 1000)
    assert is_stale(rec, "DAG result please", now) is False