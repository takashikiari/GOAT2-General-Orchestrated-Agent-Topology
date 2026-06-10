"""
hooks.py — Auto-save hook for MemoryManager.

Provides auto_save_memory() which stores the last user message and
GOAT's response into the WORKING memory tier with a timestamp.
Designed to be called after each response cycle without modifying
any existing code in memory_crud, memory_manager, or __init__.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from memory.shared.memory_manager import MemoryManager
from memory.shared.memory_enums import MemoryType

log = logging.getLogger("goat2.memory.hooks")


async def auto_save_memory(
    manager: MemoryManager,
    agent_role: str,
    user_message: str,
    goat_response: str,
    *,
    ttl: int = 3600,
) -> bool:
    """
    Save the current interaction turn into ALL three memory tiers.

    Stores in:
      - WORKING:   turn_{ts}_user / turn_{ts}_goat (with TTL)
      - EPISODIC:  full turn with user+goat content
      - LONG_TERM: full turn with user+goat content

    Returns True if WORKING save succeeded (others are best-effort).
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    combined = f"User: {user_message}\nGoat: {goat_response}"
    
    # 1. WORKING (Redis) — fast, with TTL
    try:
        await manager.store(
            agent_role,
            f"turn_{ts}_user",
            user_message,
            memory_type=MemoryType.WORKING,
            ttl=ttl,
        )
        await manager.store(
            agent_role,
            f"turn_{ts}_goat",
            goat_response,
            memory_type=MemoryType.WORKING,
            ttl=ttl,
        )
        log.debug("auto_save_memory: saved to WORKING for %s", agent_role)
    except Exception as e:
        log.warning("auto_save_memory: WORKING failed for %s: %s", agent_role, e)
        return False
    
    # 2. EPISODIC (ChromaDB) — best effort
    try:
        await manager.store(
            agent_role,
            f"turn_{ts}",
            combined,
            memory_type=MemoryType.EPISODIC,
        )
        log.debug("auto_save_memory: saved to EPISODIC for %s", agent_role)
    except Exception as e:
        log.warning("auto_save_memory: EPISODIC failed for %s: %s", agent_role, e)
    
    # 3. LONG_TERM (Letta) — best effort
    try:
        await manager.store(
            agent_role,
            f"turn_{ts}",
            combined,
            memory_type=MemoryType.LONG_TERM,
        )
        log.debug("auto_save_memory: saved to LONG_TERM for %s", agent_role)
    except Exception as e:
        log.warning("auto_save_memory: LONG_TERM failed for %s: %s", agent_role, e)
    
    return True
