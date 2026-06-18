"""Style mirror — convert a stored behavior-style profile into
a single-line system-prompt directive the LLM can pattern-match.

Pure Python, no LLM, no regex. The directive is a single
sentence that the LLM's system prompt carries verbatim:

    Learned user style — mirror it: formality: casual; tone: technical; ...

USAGE:
    from supervisor.behavior.mirror import mirror_instruction

    directive = mirror_instruction(profile_text)
    # → "Learned user style — mirror it: formality: casual; ..."

If the input is empty, has no recognized field lines, or is
otherwise invalid, the function returns ``""`` so the caller
can skip the directive entirely.
"""
from __future__ import annotations

from typing import Final

__all__ = ["PREFIX", "mirror_instruction"]

# Prefix that identifies this directive in the system prompt.
# Stable string — tests + prompts can match on it cheaply.
PREFIX: Final[str] = "Learned user style — mirror it:"


def mirror_instruction(style: str) -> str:
    """Render a ``key: value`` profile as a single-line directive.

    Args:
        style: The raw profile text (e.g. from
            ``behavior.store.load_style``). Lines without a colon
            are ignored. Empty / unrecognized input → ``""``.

    Returns:
        A single-line directive beginning with ``PREFIX`` and
        ending with a period. Empty string when the input has
        no recognized field lines.
    """
    if not style:
        return ""
    lines: list[str] = []
    for raw in style.splitlines():
        line = raw.strip()
        if not line or ":" not in line:
            continue
        k, _, v = line.partition(":")
        if k.strip() and v.strip():
            lines.append(f"{k.strip()}: {v.strip()}")
    if not lines:
        return ""
    return f"{PREFIX} {'; '.join(lines)}."
