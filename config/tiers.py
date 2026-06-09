"""Central tier registry for GOAT 2.0 memory access.

This module defines the memory tier identifiers used throughout GOAT 2.0
for memory operations across the three-tier architecture.

MEMORY TIER ARCHITECTURE:
=========================
GOAT 2.0 implements a three-tier memory system:

WORKING ("working"):
    - Session-scoped storage with TTL enforcement
    - Backed by Redis (or DictBackend for local dev)
    - Accessible by both supervisor and DAG agents
    - Used for active conversation context and DAG results

EPISODIC ("episodic"):
    - Semantic search across conversation history
    - Backed by ChromaDB with cosine HNSW indexing
    - SUPERVISOR-ONLY access (prevents memory pollution)
    - Persistent across sessions

LONG_TERM ("long_term"):
    - Core memory blocks for agent identity/behavior
    - Backed by Letta server
    - SUPERVISOR-ONLY access (prevents memory pollution)
    - Most persistent tier

ANY ("any"):
    - Special tier for search operations
    - Searches across all available tiers
    - Merges and deduplicates results
    - Read-only operation (cannot write to "any")

MEMORY ACCESS HIERARCHY:
    - GOAT_ROLE: Full access to WORKING, EPISODIC, LONG_TERM
    - SESSION_ROLE: Restricted to WORKING for DAG agents,
                    supervisor can promote to EPISODIC/LONG_TERM

All files should import from this module instead of hardcoding tier strings.
"""
from __future__ import annotations

from typing import Final

__all__ = ["WORKING", "EPISODIC", "LONG_TERM", "ANY"]

WORKING: Final[str] = "working"
"""Working memory tier — session-scoped with TTL enforcement.

Backed by Redis (or DictBackend for local dev). Accessible by both
supervisor and DAG agents. Used for active conversation context and
DAG execution results.
"""

EPISODIC: Final[str] = "episodic"
"""Episodic memory tier — semantic search across conversation history.

Backed by ChromaDB with cosine HNSW indexing. SUPERVISOR-ONLY access
to prevent memory pollution from agent-executed operations. Persistent
across sessions.
"""

LONG_TERM: Final[str] = "long_term"
"""Long-term memory tier — core memory blocks for agent identity/behavior.

Backed by Letta server. SUPERVISOR-ONLY access to prevent memory pollution.
Most persistent tier, survives across sessions and restarts.
"""

ANY: Final[str] = "any"
"""Special tier for search operations across all tiers.

Searches WORKING, EPISODIC, and LONG_TERM simultaneously.
Merges and deduplicates results. Read-only operation —
cannot write to "any" tier.
"""
