"""Text matching helpers — non-regex utilities for common patterns.

BUG-029 standardisation: the codebase policy (see
``docs/regex_policy.md``) is "no regex in supervisor / agent
modules". The ``re`` module is still used in a few lower-level
places (memory validation, time_parser, chroma helpers) where
the patterns are inherently regex-shaped — but everywhere a
simple substring, token, or boundary check suffices, use the
helpers below.

These helpers are:
  - ``substring_match``      : plain ``in`` check.
  - ``token_match``          : whole-token containment.
  - ``prefix_match``         : ``startswith`` (case-insensitive).
  - ``balanced_extract``     : find the matching ``}`` / ``]`` / ``)``
    for a given opening position.
  - ``extract_quoted_field`` : extract a ``"<key>": "<value>"``
    pair from a JSON-ish body, scanning for the next
    unescaped quote pair.
  - ``find_all_substrings``  : find all (start, end) ranges
    where a literal substring appears in the text.

All helpers are pure-Python, dependency-light, and have no
``re`` import. Each function is documented with the regex
pattern it replaces, so reviewers can confirm the replacement
is faithful.
"""
from __future__ import annotations

from typing import Final, Iterable

__all__ = [
    "substring_match",
    "token_match",
    "prefix_match",
    "balanced_extract",
    "extract_quoted_field",
    "find_all_substrings",
]


def substring_match(text: str, needle: str, *, case_sensitive: bool = True) -> bool:
    """Plain substring containment.

    Replaces: ``re.search(re.escape(needle), text)`` or
    ``needle in text``.

    Args:
        text: Source text.
        needle: Literal substring to look for.
        case_sensitive: When False, both sides are lower-cased.
    """
    if not text or not needle:
        return False
    if case_sensitive:
        return needle in text
    return needle.lower() in text.lower()


def token_match(text: str, needle: str, *, case_sensitive: bool = False) -> bool:
    """Whole-token containment.

    Splits ``text`` on whitespace and returns True when any
    token equals ``needle``. Punctuation attached to a token
    (e.g. ``"hello,"`` vs ``"hello"``) does NOT match —
    token_match is strict whole-word.

    Replaces: ``re.search(r"\\b" + re.escape(needle) + r"\\b", text)``
    when ``needle`` is a simple word.

    For multi-token patterns use ``substring_match`` (literal)
    or a custom loop.
    """
    if not text or not needle:
        return False
    if case_sensitive:
        return any(tok == needle for tok in text.split())
    n = needle.lower()
    return any(tok.lower() == n for tok in text.split())


def prefix_match(text: str, prefix: str, *, case_sensitive: bool = True) -> bool:
    """Case-sensitive (by default) prefix match.

    Replaces: ``re.match(prefix, text)`` when ``prefix`` is
    literal text without regex metacharacters.
    """
    if not text or not prefix:
        return False
    if case_sensitive:
        return text.startswith(prefix)
    return text.lower().startswith(prefix.lower())


def balanced_extract(
    text: str,
    open_pos: int,
    open_char: str = "{",
    close_char: str = "}",
) -> tuple[int, int] | None:
    """Return ``(start, end)`` of the balanced region starting
    at ``open_pos`` (where ``open_char`` is located).

    Walks the string counting nested ``open_char`` /
    ``close_char`` pairs. Skips over characters between
    quotes (single or double) so a JSON-like body containing
    ``"}{"`` inside a string value is not mis-parsed as a
    closing brace.

    Replaces: simple regex extraction of balanced braces when
    the body is JSON-shaped (no regex anchors, just counting).

    Args:
        text: Source string.
        open_pos: Index where ``open_char`` is located.
        open_char: Opening delimiter (default ``"{"``).
        close_char: Closing delimiter (default ``"}"``).

    Returns:
        ``(start, end)`` where ``start`` is the index of
        ``open_char`` and ``end`` is the index of the matching
        ``close_char``, both inclusive. ``None`` when the input
        is malformed (no balanced close found).
    """
    if not text or open_pos < 0 or open_pos >= len(text):
        return None
    if text[open_pos] != open_char:
        return None
    depth = 0
    in_str: str | None = None  # None | "'" | '"'
    i = open_pos
    while i < len(text):
        ch = text[i]
        if in_str is not None:
            if ch == "\\" and i + 1 < len(text):
                # Skip escaped char inside string.
                i += 2
                continue
            if ch == in_str:
                in_str = None
        elif ch == '"' or ch == "'":
            in_str = ch
        elif ch == open_char:
            depth += 1
        elif ch == close_char:
            depth -= 1
            if depth == 0:
                return (open_pos, i)
        i += 1
    return None


def extract_quoted_field(body: str, key: str) -> str | None:
    """Find ``"key": "value"`` in a JSON-ish body and return ``value``.

    Pure substring scan (no regex). Walks ``body`` looking for
    the literal sequence ``"<key>":<ws>"<value>"`` where
    ``<value>`` extends to the next unescaped ``"``.

    The returned value is **always** a string (the value's
    surrounding quotes are stripped, and any escaped characters
    inside the string are unescaped). For non-string values
    (numbers, booleans, null), this helper returns ``None`` —
    callers that need to handle unquoted values should implement
    a separate scan.

    Args:
        body: JSON-ish body text.
        key: Field name to find (without surrounding quotes).

    Returns:
        The unescaped ``value`` string, or ``None`` when the
        key is not found or the value is not a quoted string.
    """
    if not body or not key:
        return None
    needle = f'"{key}"'
    start = 0
    while True:
        idx = body.find(needle, start)
        if idx < 0:
            return None
        # Look for the colon + optional whitespace + opening quote.
        colon = body.find(":", idx + len(needle))
        if colon < 0:
            start = idx + 1
            continue
        # Skip whitespace between : and the value's opening quote.
        j = colon + 1
        while j < len(body) and body[j] in (" ", "\t", "\n", "\r"):
            j += 1
        if j >= len(body) or body[j] != '"':
            start = idx + 1
            continue
        # Read until the next unescaped closing quote.
        val_start = j + 1
        i = val_start
        raw_chars: list[str] = []
        while i < len(body):
            ch = body[i]
            if ch == "\\" and i + 1 < len(body):
                # Unescape the next char.
                raw_chars.append(body[i + 1])
                i += 2
                continue
            if ch == '"':
                return "".join(raw_chars)
            raw_chars.append(ch)
            i += 1
        return None  # unterminated string


def find_all_substrings(text: str, needle: str) -> Iterable[tuple[int, int]]:
    """Yield ``(start, end)`` for every occurrence of ``needle``
    in ``text``. Overlapping matches are included (mirrors
    ``re.finditer`` semantics with a plain literal pattern).

    Replaces: ``re.finditer(re.escape(needle), text)``.

    Yields:
        Tuples of (start_index, end_index_exclusive).
    """
    if not text or not needle:
        return
    start = 0
    while True:
        idx = text.find(needle, start)
        if idx < 0:
            return
        yield (idx, idx + len(needle))
        start = idx + 1
