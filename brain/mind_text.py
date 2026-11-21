"""brain.mind_text — what a stored text says, for the working memory (polish wave 2, Q1, Q4, Q5).

An agent-coord digest is stored with its fixed header and footer (telegram_interface/_coord_poller.py), which are
longer than most of its messages: ``digest_body`` keeps only the messages, so a memory line shows them and two
different digests are never taken for the same content. ``fold`` / ``has_word``: a name matches a text as a whole
word, case and diacritics aside ("Ionut" is "Ionuț"; "Ana" is not in "analiză"). ``same_open``: two open items say
the same when their normalized texts are equal or one holds the other (as whole words). Pure text functions; nothing is read or written.
"""
from __future__ import annotations

import re
import unicodedata

from memory.temporal_route import CHANNEL_DIGEST_FOOTER, CHANNEL_DIGEST_PREFIX

__all__ = ["digest_body", "fold", "has_word", "norm", "same_open"]

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


def fold(text: str) -> str:
    """``text`` lowercased, without diacritics (NFKD, combining marks removed): "Ionuț" and "IONUŢ" -> "ionut"."""
    return "".join(c for c in unicodedata.normalize("NFKD", str(text or "")) if not unicodedata.combining(c)).lower()


def has_word(text: str, name: str) -> bool:
    """True when ``name`` occurs in ``text`` as whole words (no letter or digit right before or after), folded."""
    name = " ".join(fold(name).split())
    return bool(name) and re.search(rf"(?<![^\W_]){re.escape(name)}(?![^\W_])", fold(text)) is not None


def norm(text: str) -> str:
    """``text`` folded, every run of punctuation and whitespace one space: "Compar: branch vs. main!" ->
    "compar branch vs main"."""
    return " ".join(re.sub(r"[\W_]+", " ", fold(text)).split())


def same_open(a: str, b: str) -> bool:
    """True when two open items' texts say the same: equal once normalized, or one holds the other as whole words."""
    a, b = norm(a), norm(b)
    return bool(a and b) and (a == b or f" {a} " in f" {b} " or f" {b} " in f" {a} ")
