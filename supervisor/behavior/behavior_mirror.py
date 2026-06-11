"""Format a stored behavior-style profile into a system-prompt mirroring directive."""
from __future__ import annotations

import logging
from typing import Final

log = logging.getLogger("goat2.supervisor.behavior")

__all__ = ["mirror_instruction"]

_PREFIX: Final[str] = "Learned user style — mirror it:"


def mirror_instruction(style: str) -> str:
    """
    Convert a multi-line style profile to a compact single-line directive.
    Returns '' when style is empty or contains no recognized field lines.

    Example input:
        formality: casual
        tone: technical
        language: mixed RO/EN
    Example output:
        "Learned user style — mirror it: formality: casual; tone: technical; language: mixed RO/EN."
    """
    lines = [ln.strip() for ln in style.splitlines() if ln.strip()]
    if not lines:
        return ""
    return f"{_PREFIX} {'; '.join(lines)}."
