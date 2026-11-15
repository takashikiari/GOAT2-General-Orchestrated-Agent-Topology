"""brain.rules — the cheap text rules of the memory brain v0: which lane a stored message belongs to, and what counts
as an open question or a promise for the open-items nucleus. Pure functions, no I/O (split out of brain/brain.py)."""
from __future__ import annotations

import re

__all__ = ["lane_of", "is_open_question", "is_promise"]

_DIGEST_PREFIX = "Mesaje noi pe canalul agent-coord"  # memory.temporal_route.CHANNEL_DIGEST_PREFIX (not imported)
# a promise = a committed first-person future ("revin", "o să verific", "trimit") AND a time term in the same sentence
_PROMISE = re.compile(r"\b(revin|o s[ăa] (verific|fac|caut|revin|v[ăa]d|trimit|te anun[țt])|voi (verifica|reveni|face|"
                      r"c[ăa]uta|trimite)|te anun[țt]|urmeaz[ăa] s[ăa]|trimit|fac (asta )?mai t[âa]rziu|"
                      r"m[ăa] uit (și|si) revin)\b", re.I)
_WHEN = re.compile(r"\b(azi|ast[ăa]zi|m[âa]ine|disear[ăa]|acum|imediat|cur[âa]nd|mai t[âa]rziu|peste \d+\s*(min|minute|"
                   r"ore|h)\b|la \d{1,2}([:.]\d{2})?|\+\d+\s*h|dup[ăa] (ce|restart|merge|deploy|test)|c[âa]nd (termin|vine|"
                   r"e gata))", re.I)
# quoted text is a mention, not a use: „…”, "…", “…”, «…» are dropped before matching (2026-11-15: both live
# false positives were GOAT quoting the rule's own words, e.g. 'regulile caută „revin/o să verific”')
_QUOTED = re.compile(r"„[^”]*”|“[^”]*”|\"[^\"\n]*\"|«[^»]*»")
_SENTENCES = re.compile(r"(?<=[.!?])\s+|\n+")
_QUESTION_START = re.compile(r"^(ce|cum|c[âa]nd|unde|de ce|cine|care|c[âa]t|c[âa]te|po[țt]i|e[șs]ti|avem)\b", re.I)


def lane_of(message: dict) -> str:
    """Which population a stored L2 message belongs to (human / channel / dag), from what it says about itself."""
    text = str(message.get("content") or "").lstrip()
    if message.get("role") == "user" and text.startswith(_DIGEST_PREFIX):
        return "channel"
    if message.get("role") == "user" and text.startswith("[Workflow '"):
        return "dag"
    return "human"


def is_open_question(text: str) -> bool:
    """A short message that asks something: ends with '?' or starts with a Romanian question word."""
    t = (text or "").strip()
    return 0 < len(t) < 300 and (t.endswith("?") or bool(_QUESTION_START.match(t)))


def is_promise(text: str) -> bool:
    """GOAT said it will do something, with a when: a committed future verb and a time term in the SAME sentence,
    outside quotes (a quoted word is a mention of it, not a promise)."""
    unquoted = _QUOTED.sub(" ", text or "")
    return any(_PROMISE.search(s) and _WHEN.search(s) for s in _SENTENCES.split(unquoted))
