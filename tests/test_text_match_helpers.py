"""Tests for utils.text_match — the non-regex matching helpers.

These helpers back the BUG-029 "no regex" policy in
supervisor / agents / tools / memory (see docs/regex_policy.md).
"""
from __future__ import annotations

from utils.text_match import (
    balanced_extract,
    extract_quoted_field,
    find_all_substrings,
    prefix_match,
    substring_match,
    token_match,
)


# ── substring_match ────────────────────────────────────────────────────────


def test_substring_match_basic():
    assert substring_match("hello world", "world") is True
    assert substring_match("hello world", "xyz") is False


def test_substring_match_case_insensitive():
    assert substring_match("Hello World", "WORLD", case_sensitive=False) is True
    assert substring_match("Hello World", "WORLD", case_sensitive=True) is False


def test_substring_match_empty_inputs():
    assert substring_match("", "x") is False
    assert substring_match("x", "") is False


# ── token_match ────────────────────────────────────────────────────────────


def test_token_match_basic():
    assert token_match("the cat sat", "cat") is True
    assert token_match("category", "cat") is False  # whole-word only


def test_token_match_case_insensitive_by_default():
    assert token_match("The Cat Sat", "cat") is True
    # Punctuation-attached tokens do NOT match strict token_match.
    assert token_match("The Cat, Sat", "cat") is False


def test_token_match_punctuation_handled():
    """Strict token_match rejects punctuation-attached tokens."""
    # 'world!' is a single token including the '!'; not equal
    # to 'world'. Use substring_match for punctuation-tolerant
    # matching.
    assert token_match("hello, world!", "world") is False
    assert substring_match("hello, world!", "world") is True


# ── prefix_match ───────────────────────────────────────────────────────────


def test_prefix_match_basic():
    assert prefix_match("hello world", "hello") is True
    assert prefix_match("hello world", "world") is False


def test_prefix_match_case_insensitive():
    assert prefix_match("Hello World", "HELLO", case_sensitive=False) is True


# ── balanced_extract ──────────────────────────────────────────────────────


def test_balanced_extract_simple():
    span = balanced_extract("{a: 1}", 0)
    # End is inclusive: text[5] == '}'
    assert span == (0, 5)


def test_balanced_extract_nested():
    text = "{a: {b: 2}, c: 3}"
    span = balanced_extract(text, 0)
    assert span == (0, len(text) - 1)


def test_balanced_extract_with_quote_interior():
    """A closing brace inside a quoted string is not a real close."""
    text = '{"key": "value with } brace"}'
    span = balanced_extract(text, 0)
    assert span == (0, len(text) - 1)


def test_balanced_extract_unbalanced_returns_none():
    text = "{a: 1"
    assert balanced_extract(text, 0) is None


def test_balanced_extract_custom_delimiters():
    text = "[a, [b, c], d]"
    span = balanced_extract(text, 0, open_char="[", close_char="]")
    assert span == (0, len(text) - 1)


# ── extract_quoted_field ───────────────────────────────────────────────────


def test_extract_quoted_field_basic():
    body = '{"name": "Alice", "age": 30}'
    assert extract_quoted_field(body, "name") == "Alice"
    # Non-quoted values (numbers) are not extracted by this
    # helper — the caller is expected to handle them separately.
    assert extract_quoted_field(body, "age") is None


def test_extract_quoted_field_missing():
    body = '{"name": "Alice"}'
    assert extract_quoted_field(body, "missing") is None


def test_extract_quoted_field_with_whitespace():
    body = '{ "key" :   "value"  }'
    assert extract_quoted_field(body, "key") == "value"


def test_extract_quoted_field_with_escaped_quote():
    """A backslash-escaped quote inside the value must not
    terminate the field. The helper unescapes the resulting
    value, so the unescaped form is returned."""
    body = r'"msg": "he said \"hi\""'
    # body is the literal: "msg": "he said \"hi\""
    # After unescaping: msg -> he said "hi"
    assert extract_quoted_field(body, "msg") == 'he said "hi"'


# ── find_all_substrings ───────────────────────────────────────────────────


def test_find_all_substrings_basic():
    spans = list(find_all_substrings("ababab", "ab"))
    assert spans == [(0, 2), (2, 4), (4, 6)]


def test_find_all_substrings_no_overlap():
    """Overlapping matches are included (mirrors re.finditer)."""
    text = "aaaa"
    spans = list(find_all_substrings(text, "aa"))
    assert spans == [(0, 2), (1, 3), (2, 4)]


def test_find_all_substrings_non_overlap_when_pattern_too_long():
    text = "ababab"
    spans = list(find_all_substrings(text, "ab"))
    # Each 'ab' is non-overlapping, so positions [0,2], [2,4], [4,6].
    assert spans == [(0, 2), (2, 4), (4, 6)]


def test_find_all_substrings_no_match():
    assert list(find_all_substrings("hello", "xyz")) == []


def test_find_all_substrings_empty_inputs():
    assert list(find_all_substrings("", "x")) == []
    assert list(find_all_substrings("hello", "")) == []


# ── Policy conformance: helpers must not import re ────────────────────────


def test_helpers_dont_import_re():
    """BUG-029 conformance: the text_match helpers must not
    depend on the ``re`` module."""
    import utils.text_match as tm
    src = open(tm.__file__).read()
    assert "import re" not in src, (
        "utils.text_match must not import re — it exists to "
        "replace regex usage in higher-level modules."
    )