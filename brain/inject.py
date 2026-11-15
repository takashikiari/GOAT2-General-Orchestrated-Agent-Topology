"""brain.inject — the open-items nucleus as a short block for GOAT's system prompt (Laris + GOAT, 2026-11-15).

The brain computes the nucleus every sleep cycle (brain/store.py ``_nucleus``); this turns its items into compact
text, one line per item, so the next reply knows what is still open: what GOAT said it would do, and what Laris asked
that nothing answered yet. Pure function, no I/O. An empty nucleus gives "" (no block at all, not an empty header).
"""
from __future__ import annotations

import re
import time

__all__ = ["HEADER", "format_block"]

HEADER = "[Lucruri deschise în conversație — ce ai promis și întrebări fără răspuns, cele mai noi primele]"
_KIND = {"promised": "ai promis", "unanswered": "întrebare fără răspuns"}
_HIDDEN = {"promised_old"}  # an older promise, followed by later replies: kept in the store, never shown as open
_MARKUP = re.compile(r"\*+|`+|^#+\s*|^>\s*|(?<!\w)_+|_+(?!\w)")  # an _ inside a word (post_init) stays
_SPACES = re.compile(r"\s+")


def _when(ts) -> str:
    try:
        return time.strftime("%d.%m %H:%M", time.localtime(float(ts)))
    except (TypeError, ValueError, OverflowError):
        return "?"


def _gist(text: str) -> str:
    """The item's text on one line, without markdown: the first non-empty line of the stored message."""
    first = next((ln for ln in str(text or "").splitlines() if ln.strip()), "")
    return _SPACES.sub(" ", _MARKUP.sub("", first)).strip()


def format_block(items: list[dict], max_chars: int) -> str:
    """``items`` (newest first, as the store keeps them) as HEADER + one ``- <kind> (<dd.mm HH:MM>): <text>`` line each,
    the whole block at most ``max_chars``. Items that do not fit are left out; the first one is shortened with "…"
    instead. Nothing fits, or no items → ""."""
    lines: list[str] = []
    used = len(HEADER)
    for it in items or []:
        if it.get("kind") in _HIDDEN:
            continue
        gist = _gist(it.get("text", ""))
        if not gist:
            continue
        line = f"- {_KIND.get(it.get('kind'), str(it.get('kind') or 'deschis'))} ({_when(it.get('ts'))}): {gist}"
        room = max_chars - used - 1  # 1 = the newline before the line
        if len(line) > room:
            if lines or room < 40:   # keep whole lines once one is in; never a stub of a line
                break
            line = line[: room - 1].rstrip() + "…"
        lines.append(line)
        used += 1 + len(line)
    return HEADER + "\n" + "\n".join(lines) if lines else ""
