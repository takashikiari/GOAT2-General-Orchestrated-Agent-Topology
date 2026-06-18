"""Style-analyzer marker sets — frozen word/emoji lists used by
``behavior.analyzer`` to score user style.

Pure constants, no logic. Lives in its own module so the
analyzer file stays under the 260-line cap. The markers are
the only data the scorer needs; splitting them out keeps the
analyzer focused on the scoring functions.

All words are lowercase; the analyzer case-folds input before
matching. Emoji are stored as plain strings — Python's
``str.__contains__`` handles Unicode correctly.
"""
from __future__ import annotations

from typing import Final

__all__ = [
    "POLITE_WORDS",
    "SLANG_WORDS",
    "TECH_WORDS",
    "HUMOR_WORDS",
    "SMILEY_EMOJI",
    "EMOJI_FRIENDLY",
    "CURT_WORDS",
    "FRIENDLY_EMOJI",
    "LAUGH_TOKENS",
]

POLITE_WORDS:  Final[frozenset[str]] = frozenset({
    "please", "thanks", "thank", "mulțumesc", "multumesc",
    "te", "rog", "vă", "va", "dumneavoastră", "dumneavoastra",
})

SLANG_WORDS:   Final[frozenset[str]] = frozenset({
    "yo", "sup", "wassup", "lol", "lmao", "rofl", "omg", "idk",
    "tbh", "ngl", "bruh", "bro", "dude", "y'all", "yall",
    "haide", "bă", "ba", "frate", "mă", "ma", "nasol", "fain",
})

TECH_WORDS:    Final[frozenset[str]] = frozenset({
    "api", "function", "class", "method", "error", "exception",
    "module", "package", "import", "compile", "debug", "deploy",
    "kubernetes", "docker", "regex", "thread", "async", "await",
    "request", "response", "endpoint", "json", "yaml", "toml",
    "schema", "migration", "transaction", "query", "index",
})

HUMOR_WORDS:   Final[frozenset[str]] = frozenset({
    "haha", "hehe", "hihi", "lol", "lmao", "rofl", "ahaha",
    "😂", "🤣", "😆", "😅", "😄", "😁", "😊", "🙂",
    ":)", ":-)", ";)", ";-)", ":d", "=d", "xd",
})

SMILEY_EMOJI:  Final[frozenset[str]] = frozenset({
    "🙂", "🙃", "😉", "😌", "😀", "😃", "😊", "😇", "🤗", "👍",
    "👋", "🤝", "❤️", "💚", "💙", "💛", "🧡", "💜", "🖤", "🤍",
})

EMOJI_FRIENDLY: Final[frozenset[str]] = frozenset({
    "😀", "😁", "😂", "🤣", "😊", "🙂", "😉", "👍", "❤️",
})

CURT_WORDS:    Final[frozenset[str]] = frozenset({
    "ok", "yes", "no", "da", "nu", "yep", "nope",
})

# Friendly-emoji substring (used by ``_make_notes`` to detect
# emoji usage). Stored as a string for cheap ``in`` substring
# checks rather than a frozenset — order doesn't matter and
# ``in`` over a short string is faster than ``in`` over a set.
FRIENDLY_EMOJI: Final[str] = "🙂😉😀😁😊🤗👍👋🤝"

LAUGH_TOKENS: Final[tuple[str, ...]] = ("haha", "lol", "😂")