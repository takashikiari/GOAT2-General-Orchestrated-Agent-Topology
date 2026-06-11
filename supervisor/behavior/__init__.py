"""Behavioral learning for GOAT 2.0 — style analysis, mirroring, and persistence.

Exports:
    - analyze_style: Infer user communication style from turns
    - mirror_instruction: Format style profile for system prompt
    - BehaviorProfile: TypedDict for style fields
    - serialize/deserialize: Convert between profile and text
    - finalize_behavior: Session-end style lifecycle
    - load_style/save_style: Letta 'persona' block persistence
    - maybe_store_info: Extract facts from user messages
    - ScoredFact, INFERRED_TTL: Fact confidence types
"""
from __future__ import annotations

import logging

log = logging.getLogger("goat2.supervisor.behavior")

from supervisor.behavior.behavior_analyzer import analyze_style
from supervisor.behavior.behavior_mirror import mirror_instruction
from supervisor.behavior.behavior_profile import BehaviorProfile, serialize, deserialize, empty_profile
from supervisor.behavior.behavior_session import finalize_behavior
from supervisor.behavior.behavior_store import load_style, save_style
from supervisor.behavior.info_extract import maybe_store_info
from supervisor.behavior.info_types import ScoredFact, INFERRED_TTL

__all__ = [
    "analyze_style",
    "mirror_instruction",
    "BehaviorProfile",
    "serialize",
    "deserialize",
    "empty_profile",
    "finalize_behavior",
    "load_style",
    "save_style",
    "maybe_store_info",
    "ScoredFact",
    "INFERRED_TTL",
]