"""brain.mind_text — what a stored text says, for the working memory (polish wave 2, Q1).

An agent-coord digest is stored with its fixed header and footer (telegram_interface/_coord_poller.py), which are
longer than most of its messages: ``digest_body`` keeps only the messages, so a memory line shows them and two
different digests are never taken for the same content. Pure text functions; nothing is read or written.
"""
from __future__ import annotations

import re

from memory.temporal_route import CHANNEL_DIGEST_FOOTER, CHANNEL_DIGEST_PREFIX

__all__ = ["digest_body"]

_MSG = re.compile(r"\[[^\]\n]{1,120} -> [^\]\n]{1,120}\]")  # a digest message: "[from -> to] „text”"
# the footer runs to the end of the user turn: what follows it (the stored reply) stays
_FOOTER = re.compile(r"\s*" + re.escape(CHANNEL_DIGEST_FOOTER) + r".*?(?=\n\s*assistant\s*:|\Z)", re.S | re.I)


def digest_body(text: str) -> str:
    """``text`` with an agent-coord digest's fixed header and footer removed (what came before the header, a
    timestamp or role, stays); any other text unchanged. Without a message line after the header, the header runs
    to its blank line (or to the end: a digest cut inside its header says nothing)."""
    text = text or ""
    i = text.find(CHANNEL_DIGEST_PREFIX, 0, 200)
    if i < 0:
        return text
    m, end = _MSG.search(text, i), text.find("\n\n", i)
    start = m.start() if m else (end + 2 if end >= 0 else len(text))
    return _FOOTER.sub("", text[:i] + text[start:])
