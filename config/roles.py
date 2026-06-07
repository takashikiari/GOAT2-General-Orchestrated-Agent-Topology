"""Central role registry for GOAT 2.0 memory access control.

This module defines the two primary role identifiers used throughout GOAT 2.0
for memory tier access control and identity management.

GOAT_ROLE ("goat"):
    Supervisor identity role with full memory tier access.
    Used for:
    - Behavior profile storage (Letta persona block)
    - User profile management (Letta human block)
    - Fact extraction and storage (info_extract)
    - Direct memory queries with full tier access
    - All supervisor-level memory operations

SESSION_ROLE ("user_session"):
    Session-scoped role for conversation and DAG execution memory.
    Used for:
    - Conversation turn storage (store_turn)
    - DAG result persistence (store_dag_result)
    - Working memory operations during DAG execution
    - Session summary storage
    - Memory injection for conversational context
    - DAG agent memory access (restricted to working tier)

MEMORY ACCESS HIERARCHY:
    - GOAT_ROLE: Full access to WORKING, EPISODIC, LONG_TERM tiers
    - SESSION_ROLE: Restricted to WORKING tier for DAG agents,
                    supervisor can promote to EPISODIC/LONG_TERM

All files should import from this module instead of hardcoding role strings.
"""
from __future__ import annotations

from typing import Final

__all__ = ["GOAT_ROLE", "SESSION_ROLE"]

GOAT_ROLE: Final[str] = "goat"
"""Supervisor identity role with full memory tier access.

Used for behavior profiles, user profiles, fact extraction, and all
supervisor-level memory operations across WORKING, EPISODIC, and LONG_TERM tiers.
"""

SESSION_ROLE: Final[str] = "user_session"
"""Session-scoped role for conversation and DAG execution memory.

Used for conversation turns, DAG results, working memory operations, and
session context. DAG agents restricted to WORKING tier; supervisor can
promote to EPISODIC and LONG_TERM.
"""
