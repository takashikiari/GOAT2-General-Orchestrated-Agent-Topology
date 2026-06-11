from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Literal

log = logging.getLogger("goat2.memory.shared")

__all__ = ["MemoryType", "MemoryTierLiteral", "LayerStatus"]

# Rust equivalent: enum MemoryTier { Working, Episodic, LongTerm }
MemoryTierLiteral = Literal["working", "episodic", "long_term"]


class MemoryType(str, Enum):
    """The three memory tiers in GOAT 2.0. Inherits str so values equal MemoryTierLiteral strings."""
    WORKING   = "working"
    EPISODIC  = "episodic"
    LONG_TERM = "long_term"

    @staticmethod
    def priority_order() -> list[MemoryTierLiteral]:
        return ["working", "episodic", "long_term"]


@dataclass(slots=True)
class LayerStatus:
    """Health snapshot returned by MemoryManager.status(). All three fields are True on a healthy run."""
    working:   bool
    episodic:  bool
    long_term: bool

    @property
    def all_healthy(self) -> bool:
        return self.working and self.episodic and self.long_term

    @property
    def any_healthy(self) -> bool:
        return self.working or self.episodic or self.long_term

    def to_dict(self) -> dict[str, bool]:
        return {
            "working":     self.working,
            "episodic":    self.episodic,
            "long_term":   self.long_term,
            "all_healthy": self.all_healthy,
        }

    def __repr__(self) -> str:
        flags = {
            "W": "✓" if self.working   else "✗",
            "E": "✓" if self.episodic  else "✗",
            "L": "✓" if self.long_term else "✗",
        }
        return (
            f"LayerStatus(working={flags['W']} "
            f"episodic={flags['E']} long_term={flags['L']})"
        )
