#!/usr/bin/env python3
"""Configure working memory (tier=working) with relevant session data.

Usage:
    python scripts/configure_working_memory.py

This script:
1. Initializes the ServiceRegistry (DI container)
2. Sets the global registry for tool access
3. Stores key session context into working memory (Redis-backed)
4. Verifies stored data via retrieval
5. Outputs configuration steps and stored data

Prerequisites:
    - Redis server running (or falls back to DictBackend)
    - GOAT 2.0 environment configured
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Bootstrap: registry + global accessor
# ---------------------------------------------------------------------------

from config.registry import ServiceRegistry
from tools.registry_accessor import set_registry, get_registry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-24s  %(levelname)s  %(message)s",
)
log = logging.getLogger("configure_working_memory")

# Suppress noisy debug logs from memory internals
logging.getLogger("goat2.memory").setLevel(logging.WARNING)
logging.getLogger("goat2.config").setLevel(logging.WARNING)


async def main() -> None:
    """Main entry point: configure working memory and display results."""
    print("=" * 72)
    print("  GOAT 2.0 — Working Memory Configuration (tier=working)")
    print("=" * 72)

    # -----------------------------------------------------------------------
    # STEP 1: Initialize ServiceRegistry
    # -----------------------------------------------------------------------
    print("\n[STEP 1] Initializing ServiceRegistry (dependency injection)...")
    registry = ServiceRegistry()
    set_registry(registry)
    print(f"  ✓ Registry ready: {registry!r}")

    # -----------------------------------------------------------------------
    # STEP 2: Verify memory manager health
    # -----------------------------------------------------------------------
    print("\n[STEP 2] Verifying memory tier health...")
    status = await registry.memory_manager.status()
    print(f"  ✓ Working:   {'✓' if status.working else '✗'}")
    print(f"  ✓ Episodic:  {'✓' if status.episodic else '✗'}")
    print(f"  ✓ Long-term: {'✓' if status.long_term else '✗'}")
    print(f"  ✓ All healthy: {status.all_healthy}")

    # -----------------------------------------------------------------------
    # STEP 3: Store session context into working memory
    # -----------------------------------------------------------------------
    print("\n[STEP 3] Storing session context into working memory...")

    now = datetime.now(timezone.utc).isoformat()
    session_id = f"session-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"

    # 3a. Session metadata
    await registry.memory_manager.store(
        agent_role="goat",
        key="session:current",
        content=(
            f"session_id: {session_id}\n"
            f"started_at: {now}\n"
            f"language: ro\n"
            f"tier: working\n"
            f"status: active"
        ),
        memory_type="working",
        ttl=3600,  # 1 hour TTL
    )
    print(f"  ✓ Stored: session:current → {session_id}")

    # 3b. User preferences (language = Romanian)
    await registry.memory_manager.store(
        agent_role="goat",
        key="user:preferences",
        content=(
            "language: ro\n"
            "response_language: Romanian\n"
            "memory_tier: working\n"
            "session_type: interactive"
        ),
        memory_type="working",
        ttl=3600,
    )
    print("  ✓ Stored: user:preferences → language=ro")

    # 3c. Current task context
    await registry.memory_manager.store(
        agent_role="goat",
        key="task:current",
        content=(
            "task: configure_working_memory\n"
            "description: Populate working memory with session-relevant data\n"
            "status: in_progress\n"
            "target_tier: working\n"
            "agent_role: goat"
        ),
        memory_type="working",
        ttl=3600,
    )
    print("  ✓ Stored: task:current → configure_working_memory")

    # 3d. Agent role context
    await registry.memory_manager.store(
        agent_role="goat",
        key="agent:context",
        content=(
            "role: coder\n"
            "capabilities: memory_configuration, code_generation, system_analysis\n"
            "active_tier: working\n"
            "session_mode: configuration"
        ),
        memory_type="working",
        ttl=3600,
    )
    print("  ✓ Stored: agent:context → role=coder")

    # 3e. Memory system configuration snapshot
    await registry.memory_manager.store(
        agent_role="goat",
        key="memory:config",
        content=(
            "working_backend: RedisBackend\n"
            "episodic_backend: ChromaMemoryClient\n"
            "long_term_backend: LettaClient\n"
            "default_ttl: 3600\n"
            "tier: working\n"
            "role: goat"
        ),
        memory_type="working",
        ttl=3600,
    )
    print("  ✓ Stored: memory:config → system configuration snapshot")

    # -----------------------------------------------------------------------
    # STEP 4: Verify stored data via retrieval
    # -----------------------------------------------------------------------
    print("\n[STEP 4] Verifying stored data (retrieval)...")

    verify_keys = [
        "session:current",
        "user:preferences",
        "task:current",
        "agent:context",
        "memory:config",
    ]

    for key in verify_keys:
        entry = await registry.memory_manager.retrieve(
            agent_role="goat",
            key=key,
            memory_type="working",
        )
        if entry and entry.ok:
            print(f"  ✓ Retrieved: {key}")
            # Show first line of content
            first_line = entry.content.split("\n")[0]
            print(f"    └─ {first_line}")
        else:
            print(f"  ✗ MISSING: {key}")

    # -----------------------------------------------------------------------
    # STEP 5: List all working memory entries
    # -----------------------------------------------------------------------
    print("\n[STEP 5] Listing all working memory entries...")
    entries = await registry.memory_manager.list(
        agent_role="goat",
        memory_type="working",
        limit=20,
    )
    print(f"  Total entries: {len(entries)}")
    for entry in entries:
        print(f"  [{entry.source}] {entry.key}: {entry.content[:80]}...")

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    print("\n" + "=" * 72)
    print("  CONFIGURATION COMPLETE")
    print("=" * 72)
    print(f"\n  Working memory populated with {len(verify_keys)} key-value pairs.")
    print(f"  Session ID: {session_id}")
    print(f"  Language: Romanian (ro)")
    print(f"  TTL: 3600s (1 hour)")
    print(f"  Backend: {type(registry.working_memory.backend).__name__}")
    print(f"  Memory Manager: {type(registry.memory_manager).__name__}")
    print()

    # -----------------------------------------------------------------------
    # Stored data summary (as requested: output the stored data)
    # -----------------------------------------------------------------------
    print("─" * 72)
    print("  STORED DATA DUMP")
    print("─" * 72)
    for key in verify_keys:
        entry = await registry.memory_manager.retrieve(
            agent_role="goat",
            key=key,
            memory_type="working",
        )
        if entry:
            print(f"\n  [{key}]")
            for line in entry.content.split("\n"):
                print(f"    {line}")


if __name__ == "__main__":
    asyncio.run(main())
