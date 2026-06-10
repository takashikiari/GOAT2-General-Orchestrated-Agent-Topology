"""Fact confidence types for info_extract — Rust-ready, single source of truth."""
from __future__ import annotations

from typing import Final, Literal, TypedDict

__all__ = ["FactKind", "ScoredFact", "INFERRED_TTL"]

FactKind = Literal["explicit", "inferred"]

INFERRED_TTL: Final[int] = 7 * 24 * 3600  # 7 days in seconds


class ScoredFact(TypedDict):
    """One extracted fact with confidence classification. No dict[str, Any]."""
    key:   str
    value: str
    kind:  FactKind
