"""Test for rule 14: when deleting/modifying memory entries, use EXACT keys
from prior tool results — never pattern-match or construct key formats.

Diagnostic observation (2026-06-20): At "Șterge pe alea cu baaa" GOAT called
``memory_search(query="baaa")`` ONCE, got results with real keys, then
ignored those keys and invented 5 keys of the form ``turn_<ts>_<n>`` (wrong
pattern — real keys are ``turn:N:intent`` / ``turn:N:summary``). All 5
deletes returned "Key not found"; the turn hit the 6-tool cap without
deleting anything.

Root cause: GOAT_SYSTEM had no rule forbidding pattern-matching key
formats. Rule 14 pins the correct behaviour: read the literal key string
from a tool result this turn, or you don't have the key.

This test asserts two things:
  1. The rule is present in GOAT_SYSTEM (regression guard).
  2. The rule explicitly forbids constructing/guessing/pattern-matching
     key formats — so a future rewriter cannot replace "search first"
     with "infer the format".
"""
from __future__ import annotations

from supervisor.identity import GOAT_SYSTEM


def test_rule_14_present():
    """GOAT_SYSTEM must contain a numbered rule 14 that governs
    memory delete/modify behaviour: search first, use exact keys."""
    assert "14." in GOAT_SYSTEM, "rule 14 marker missing from GOAT_SYSTEM"


def test_rule_14_requires_search_before_delete():
    """Rule 14 must explicitly require calling memory_search or
    memory_recent BEFORE deleting or modifying memory entries."""
    haystack = GOAT_SYSTEM.lower()
    # Must mention search or recent (the only ways to discover real keys)
    has_search_or_recent = "search" in haystack or "recent" in haystack
    # Must mention delete or modify (the destructive operation)
    has_destructive_op = "delete" in haystack or "modify" in haystack
    # Must say "before" or "first" (temporal ordering)
    has_temporal_order = "before" in haystack or "first" in haystack

    assert has_search_or_recent and has_destructive_op and has_temporal_order, (
        "rule 14 must require search/recent BEFORE delete/modify. "
        f"search_or_recent={has_search_or_recent} "
        f"destructive={has_destructive_op} "
        f"temporal={has_temporal_order}"
    )


def test_rule_14_forbids_constructing_key_formats():
    """Rule 14 must explicitly forbid the model from constructing,
    guessing, or pattern-matching key formats. The bug was exactly
    this: GOAT pattern-matched ``turn_<ts>_<n>`` instead of reading
    the literal ``turn:N:intent`` keys from the search result.

    This is the key anti-regression assertion: a future rewriter
    could keep "search first" wording while removing the explicit
    prohibition against pattern-matching. The bug would then silently
    return. We forbid that rewrite here.
    """
    haystack = GOAT_SYSTEM.lower()
    # At least one of these prohibitions must appear: "construct",
    # "guess", "pattern-match", or "never invent" (in the key context).
    forbids_construction = (
        "construct" in haystack
        or "guess" in haystack
        or "pattern" in haystack
    )
    assert forbids_construction, (
        "rule 14 must explicitly forbid the model from constructing, "
        "guessing, or pattern-matching key formats. Without this, GOAT "
        "will continue to invent keys like 'turn_<ts>_<n>' when the "
        "real keys are 'turn:N:intent' / 'turn:N:summary'."
    )


def test_rule_14_does_not_replace_rules_12_and_13():
    """Adding rule 14 must not silently remove rules 12 and 13 —
    they are independent tool-loop guards and must all coexist."""
    # Pull the rule 14 block (everything from "14." to "15." or end).
    text = GOAT_SYSTEM
    start = text.find("14.")
    assert start != -1, "rule 14 not found"
    end = text.find("15.", start)
    rule_14 = text[start:end if end != -1 else len(text)]

    # Rules 12 and 13 are independent — verify they're still present
    # in the text outside of rule 14.
    outside = text[:start] + (text[end:] if end != -1 else "")
    assert "12." in outside, "rule 12 was removed when rule 14 was added"
    assert "13." in outside, "rule 13 was removed when rule 14 was added"

    # And the per-tool-failure guidance from rule 13 must NOT be
    # weakened by rule 14 — the old rule said "don't retry failed
    # calls more than once". Rule 14's emphasis on "use exact keys
    # from results" is a separate concern.
    assert "retry" in outside.lower(), (
        "rule 13 (limit retries of failed tool calls) was lost"
    )
