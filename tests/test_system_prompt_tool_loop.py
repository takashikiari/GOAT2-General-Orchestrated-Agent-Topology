"""Tests for the tool-loop rules added to GOAT_SYSTEM.

Diagnostic observation: GOAT is lucid in normal conversation but
breaks inside the tool-calling loop — it either keeps calling
tools pointlessly or hits the per-turn cap. The root cause is
that ``GOAT_SYSTEM`` had no rules governing behaviour INSIDE the
loop. Rule 8 ("always provide a visible response") covers the
output, but nothing covers when to STOP calling tools or how
to handle repeated failures.

Two new rules address this:

  12. Stop calling tools once you have enough information to
      answer the user.
  13. If a tool call fails or returns no useful result, do not
      retry the same call more than once — change approach or
      report the failure.

These tests pin the new rules in place so they cannot silently
disappear.
"""
from __future__ import annotations

from supervisor.identity import GOAT_SYSTEM


def test_stop_calling_tools_rule_present():
    """GOAT_SYSTEM must include a rule that tells the model to
    stop calling tools once it has enough information to answer.

    The exact wording can vary, but the key concept must appear:
    (a) a "stop" / "enough" trigger, and (b) a reference to tools
    or tool calls.
    """
    haystack = GOAT_SYSTEM.lower()
    assert (
        ("stop" in haystack and "tool" in haystack and "enough" in haystack)
        or ("stop calling" in haystack and "tool" in haystack)
    ), (
        "GOAT_SYSTEM must include a rule that says: stop calling "
        "tools once you have enough information. Without this rule "
        "the model has been observed to burn the per-turn tool-call "
        "cap on calls that return no new information."
    )


def test_no_retry_failed_tools_rule_present():
    """GOAT_SYSTEM must include a rule that limits retries of
    failed tool calls — the model must change approach or
    report the failure rather than retrying the same call."""
    haystack = GOAT_SYSTEM.lower()
    assert (
        ("retry" in haystack or "repeat" in haystack)
        and "tool" in haystack
        and ("once" in haystack or "different" in haystack or "change" in haystack)
    ), (
        "GOAT_SYSTEM must include a rule limiting retries of "
        "failed tool calls. Observed pattern: model calls "
        "memory_get for non-existent keys 5+ times in a row "
        "without changing approach."
    )


def test_new_tool_rules_do_not_replace_existing_rules():
    """The new rules must be ADDED, not replace rules 1-11."""
    # Foundation rules that must remain.
    assert "Never invent facts" in GOAT_SYSTEM
    assert "Use memory as context" in GOAT_SYSTEM
    # The Commit 1.5 self-report rule (rule 11) must also remain.
    assert "action log" in GOAT_SYSTEM.lower(), (
        "rule 11 (self-report from action log) was lost — "
        "this indicates the GOAT_SYSTEM rewrite clobbered it"
    )


def test_new_rules_have_rule_numbers():
    """The new rules must be numbered (12 and 13) so they
    can be referenced in test assertions and operator logs."""
    assert "12." in GOAT_SYSTEM, "rule 12 marker missing"
    assert "13." in GOAT_SYSTEM, "rule 13 marker missing"


def test_tool_rules_positioned_near_rule_8():
    """The new tool-loop rules should sit near rule 8 (the
    existing tool-output rule: 'After tool calls, always
    provide a visible response') so the model reads them
    together as one block."""
    lines = GOAT_SYSTEM.split("\n")
    pos_8 = next(
        (i for i, l in enumerate(lines) if "8." in l and "tool" in l.lower()),
        None,
    )
    pos_12 = next(
        (i for i, l in enumerate(lines) if l.startswith("12.")),
        None,
    )
    pos_13 = next(
        (i for i, l in enumerate(lines) if l.startswith("13.")),
        None,
    )
    assert pos_8 is not None, "rule 8 not found (should reference 'tool calls')"
    assert pos_12 is not None and pos_13 is not None, "rules 12 and 13 not found"
    # Rules 12 and 13 must be after rule 8 (else the numbering is broken).
    assert pos_8 < pos_12 < pos_13, (
        f"rules must be in order: 8@{pos_8} < 12@{pos_12} < 13@{pos_13}"
    )
    # And close to rule 8 (within a few lines — they're a logical block).
    assert pos_13 - pos_8 <= 8, (
        f"rule 13 at line {pos_13} should be within ~8 lines of "
        f"rule 8 at line {pos_8} (distance {pos_13 - pos_8})"
    )
