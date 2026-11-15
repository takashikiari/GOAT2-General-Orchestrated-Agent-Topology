"""brain.rules — the cheap text rules of the memory brain v0: which lane a stored message belongs to, and what counts
as an open question or a promise for the open-items nucleus. Pure functions, no I/O (split out of brain/brain.py)."""
from __future__ import annotations

import re

__all__ = ["lane_of", "is_open_question", "is_promise"]

_DIGEST_PREFIX = "Mesaje noi pe canalul agent-coord"  # memory.temporal_route.CHANNEL_DIGEST_PREFIX (not imported)
_PROMISE = re.compile(r"\b(revin|o s[ăa] (verific|fac|caut|revin|v[ăa]d|te anun[țt])|voi (verifica|reveni|face|"
                      r"c[ăa]uta)|te anun[țt]|urmeaz[ăa] s[ăa]|fac (asta )?mai t[âa]rziu|m[ăa] uit (și|si) revin)\b", re.I)
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
    return bool(_PROMISE.search(text or ""))
