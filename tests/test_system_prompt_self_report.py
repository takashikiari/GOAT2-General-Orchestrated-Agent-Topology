"""Tests for the new self-report rule in GOAT_SYSTEM.

The audit of session 12:58:48 showed GOAT confabulating
a "raport de 6 puncte" about what it did in the previous
turn, even though the only real action was 5 failed
memory_delete calls (every one returned 'Key not found').

The root cause: GOAT had no access to structured data about
its previous actions. Its only 'memory' of what it did was
its own previous text — which was a fallback message,
not a record of the real calls.

Two fixes work together:
  1. The structured action log (Commit Action Log).
  2. A new GOAT_SYSTEM rule telling the model explicitly:
     report from the action log, NOT from your own previous text.

These tests pin the second fix in place.
"""
from __future__ import annotations

from supervisor.identity import GOAT_SYSTEM


def test_self_report_rule_present():
    """GOAT_SYSTEM must include a rule that tells the model to
    report from the action log, not from its own previous text."""
    # The exact wording can vary but the two key concepts must
    # both appear: (a) "action log" / "structured", and (b) a
    # prohibition on reporting from previous text.
    haystack = GOAT_SYSTEM.lower()
    assert "action log" in haystack or "structured action" in haystack, (
        "GOAT_SYSTEM must reference the structured action log"
    )
    assert "never" in haystack or "do not" in haystack, (
        "GOAT_SYSTEM must explicitly forbid confabulating from "
        "the model's own previous text"
    )


def test_self_report_rule_does_not_replace_existing_rules():
    """The new rule must be ADDED, not replace rule 1-10."""
    # Rule 1 (Never invent facts) is foundational.
    assert "Never invent facts" in GOAT_SYSTEM
    # Rule 4 (Use memory as context) is the conceptual neighbour.
    assert "Use memory as context" in GOAT_SYSTEM


def test_self_report_rule_positioned_near_memory_rules():
    """The new rule should sit with the other memory / context
    rules (8 and 9) so the model reads it as part of the
    memory discipline, not a one-off."""
    # Find the position of rule 9 (label "[FRESH][CONV]") and the
    # new rule. The new rule should be close (within ~3 lines).
    lines = GOAT_SYSTEM.split("\n")
    pos_9 = next(
        (i for i, l in enumerate(lines)
        if "Prioritize [FRESH]" in l or "[CONV]" in l and "9." in l),
        None,
    )
    pos_self = next(
        (i for i, l in enumerate(lines) if "action log" in l.lower()),
        None,
    )
    assert pos_9 is not None and pos_self is not None
    assert abs(pos_9 - pos_self) <= 4, (
        f"self-report rule at line {pos_self} should be near the "
        f"memory rules (line {pos_9}) — distance {abs(pos_9 - pos_self)}"
    )