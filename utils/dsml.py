"""DSML marker stripping — single canonical implementation.

DeepSeek (and some other models) emit DeepSeek-ML control tags like
    <｜｜DSML｜｜function_calls｜｜>…</｜｜DSML｜｜function_calls｜｜>
which are useful for tool routing upstream but should never reach the
final user (CLI, Telegram, history replay) or be passed back into a
subsequent LLM turn. All callers in supervisor, session history, and
CLI use this single function.

Kept in its own tiny module so ``utils/llm_utils`` stays under the
260-line single-responsibility rule while the regex set remains easy
to inspect / extend in one place.
"""
from __future__ import annotations

import re

__all__ = ["strip_dsml"]

# Paired wrapper: <tag>...</tag> (DOTALL so bodies can span lines).
_DSML_PAIR_RE = re.compile(
    r"<\｜｜DSML｜｜\w+>[^<]*</\｜｜DSML｜｜\w+>", re.DOTALL
)
# Orphan opening tag: <｜｜DSML｜｜...>
_DSML_OPEN_RE = re.compile(r"<\｜｜DSML｜｜[^>]*>")
# Orphan closing fragment: </｜｜DSML｜｜...> (slash-prefixed)
_DSML_CLOSE_RE = re.compile(r"</\｜｜DSML｜｜[^>]*>")
# Bare `/DSMLxxx` tail fragments the model occasionally emits.
_DSML_TAIL_RE = re.compile(r"/DSML[A-Za-z_]*")


def strip_dsml(text: str) -> str:
    """Remove DeepSeek DSML markers from text. Single canonical implementation.

    Strips, in order: paired DSML wrapper blocks, orphan opening tags,
    orphan closing tags, and bare ``/DSMLxxx`` tail fragments. Pure
    function — no I/O, no LLM.

    Args:
        text: Source text that may contain DSML markers.

    Returns:
        Cleaned text with all DSML markers removed and edges trimmed.
    """
    if not text:
        return text
    text = _DSML_PAIR_RE.sub("", text)
    text = _DSML_OPEN_RE.sub("", text)
    text = _DSML_CLOSE_RE.sub("", text)
    text = _DSML_TAIL_RE.sub("", text)
    return text.strip()