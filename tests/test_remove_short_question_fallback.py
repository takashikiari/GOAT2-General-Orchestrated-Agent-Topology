"""Tests for BUG-019 fix: remove the short+? clarify fallback.

The previous ``_classify_response`` had a fallback: any response
of ≤ 100 chars ending in ``?`` with no tools called was treated
as a clarifying question. A short, rhetorical ``?`` reply such
as ``"Should we use Flask or FastAPI?"`` would be misclassified
as ``"clarify"`` and the LLM would emit a generic "could you
rephrase" instead of answering the question.

The fix removes the fallback. Clarification is now an explicit
signal — the LLM must end its reply with the ``[CLARIFY]``
marker. Anything else is a direct reply (or a DAG spawn).
"""
from __future__ import annotations

import supervisor.pipeline.goat_call as gc
from supervisor.pipeline.prompt_helpers import _CLARIFY_MARKER


def _classify(text: str, called: tuple = ()):
    return gc._classify_response(text, called)


# ── Explicit [CLARIFY] marker still works ───────────────────────────────────


def test_explicit_clarify_marker_classifies_as_clarify():
    action, visible = _classify("what do you mean? [CLARIFY]")
    assert action == "clarify"
    assert visible == "what do you mean?"


def test_explicit_clarify_marker_case_insensitive():
    action, visible = _classify("hi [clarify]")
    assert action == "clarify"
    assert visible == "hi"


# ── BUG-019: short + ? no longer triggers clarify fallback ─────────────────


def test_short_question_no_tools_is_direct_not_clarify():
    """A short, rhetorical '?' reply (no tools, no marker) is a
    direct reply — not a clarifying question."""
    action, _ = _classify("Should we use Flask or FastAPI?")
    assert action == "direct", (
        f"BUG-019: short+? without [CLARIFY] marker should be 'direct', "
        f"got {action!r}"
    )


def test_single_word_question_is_direct():
    action, _ = _classify("Why?")
    assert action == "direct"


def test_long_question_is_direct():
    action, _ = _classify(
        "Could you walk me through the trade-offs between these two "
        "approaches in the context of our existing architecture?"
    )
    assert action == "direct"


# ── DAG and direct still work ──────────────────────────────────────────────


def test_dag_action_when_start_dag_tool_called():
    action, _ = _classify("anything", called=("start_dag",))
    assert action == "dag"


def test_direct_action_for_normal_text():
    action, visible = _classify("The answer is 42.")
    assert action == "direct"
    assert visible == "The answer is 42."


# ── Static check: the fallback is removed from _classify_response ─────────


def test_static_check_no_short_question_fallback():
    """Sanity: ``_classify_response`` no longer has the buggy
    ``not called and len(stripped) <= 100 and stripped.endswith("?")``
    composite condition."""
    import inspect
    src = inspect.getsource(gc._classify_response)
    # The original buggy line bundled three conditions. The fix
    # removes the entire ``endswith("?")`` branch — we verify by
    # checking that the composite branch is gone (a small
    # tolerance for the marker suffix strip, which legitimately
    # uses endswith on the marker token, not on '?').
    assert 'stripped.endswith("?")' not in src, (
        "_classify_response still contains the 'stripped.endswith(\"?\")' "
        "short-question fallback — BUG-019 not fully fixed."
    )
    # And the marker check remains.
    assert _CLARIFY_MARKER in src, (
        "_classify_response must still recognise the [CLARIFY] marker."
    )