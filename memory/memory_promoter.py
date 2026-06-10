"""Memory Promoter — Automatic memory tier promotion.

Handles automatic promotion of conversation turns between memory tiers:
- WORKING → EPISODIC (ChromaDB): Turn 2+ (messages >= 4)
- EPISODIC → LONG_TERM (Letta): Turn 3+ (messages >= 6)

TOOL DISTRIBUTION:
=================
- DAG agents: FILE_TOOLS + WEB_SEARCH + DAG_MEMORY_TOOLS (dag:* namespace)
- GOAT CONVERSATIONAL: FILE_TOOLS + MEMORY_TOOLS (all tiers, goat:* namespace)
- GOAT VALIDATOR: direct memory_manager access only, no tool calls
- GOAT Memory Promoter: direct memory_manager.promote() only

ARCHITECTURE NOTE:
================
MemoryPromoter is a pipeline component that handles automatic
tier promotion. It's distinct from supervisor's _schedule_promotion
because it provides direct memory_manager.promote() access.

This is distinct from memory.shared.hooks because:
- hooks.py: Provides auto_save_memory for turn-based saving
- memory_promoter.py: Provides tier-specific promotion logic
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final

from config.roles import SESSION_ROLE

if TYPE_CHECKING:
    from memory.shared import MemoryManager

log = logging.getLogger("goat2.memory_promoter")

__all__ = ["MemoryPromoter"]

# Promotion turn thresholds (matching memory_manager.promote_turns)
# These determine when to promote between tiers
# NOTE: turn_count >= 4 means 4+ messages, turn_count >= 6 means 6+ messages
EPISODIC_THRESHOLD: Final[int] = 4  # Turn 2+ (messages >= 4)
LONG_TERM_THRESHOLD: Final[int] = 6  # Turn 3+ (messages >= 6)


class MemoryPromoter:
    """Automatic memory tier promotion handler.

    Manages automatic promotion of conversation turns between
    memory tiers based on turn count.

    TOOL DISTRIBUTION:
    ==================
    - DAG agents: FILE_TOOLS + WEB_SEARCH + DAG_MEMORY_TOOLS (dag:* namespace)
    - GOAT CONVERSATIONAL: FILE_TOOLS + MEMORY_TOOLS (all tiers, goat:* namespace)
    - GOAT VALIDATOR: direct memory_manager access only, no tool calls
    - GOAT Memory Promoter: direct memory_manager.promote() only

    PROMOTION RULES:
    ===============
    - Turn 2+ (messages >= 4): WORKING → EPISODIC, keep_source=True
    - Turn 3+ (messages >= 6): EPISODIC → LONG_TERM, keep_source=False

    Example:
        promoter = MemoryPromoter(memory_manager)
        # Check if promotion needed
        if promoter.should_promote_to_episodic(turn_count):
            await promoter.promote_to_episodic(turn_count)
    """

    def __init__(self, memory_manager: "MemoryManager") -> None:
        """Initialize MemoryPromoter with memory manager.

        Args:
            memory_manager: MemoryManager for tier access.
        """
        self._mm = memory_manager

    def should_promote_to_episodic(self, turn_count: int) -> bool:
        """Check if should promote to episodic tier.

        Promotion to EPISODIC (ChromaDB) happens when:
        - Turn count >= EPISODIC_THRESHOLD (default 2, meaning messages >= 4)

        Args:
            turn_count: Current turn count in conversation.

        Returns:
            True if should promote to episodic tier.
        """
        return turn_count >= EPISODIC_THRESHOLD

    def should_promote_to_longterm(self, turn_count: int) -> bool:
        """Check if should promote to long-term tier.

        Promotion to LONG_TERM (Letta) happens when:
        - Turn count >= LONG_TERM_THRESHOLD (default 3, meaning messages >= 6)

        Args:
            turn_count: Current turn count in conversation.

        Returns:
            True if should promote to long-term tier.
        """
        return turn_count >= LONG_TERM_THRESHOLD

    async def promote_to_episodic(self, turn_count: int) -> bool:
        """Promote working memory to episodic tier.

        Uses memory_manager.promote() with keep_source=True to
        preserve source attribution in ChromaDB.

        Args:
            turn_count: Current turn count for logging.

        Returns:
            True if promotion succeeded, False otherwise.
        """
        if not self._mm:
            log.warning("MemoryPromoter: no memory_manager available")
            return False

        try:
            await self._mm.promote(
                SESSION_ROLE,
                keep_source=True,
            )
            log.info(
                "MemoryPromoter: promoted to EPISODIC at turn %d",
                turn_count,
            )
            return True
        except Exception as e:
            log.error(
                "MemoryPromoter: promote_to_episodic failed at turn %d: %s",
                turn_count, e,
            )
            return False

    async def promote_to_longterm(self, turn_count: int) -> bool:
        """Promote episodic memory to long-term tier.

        Uses memory_manager.promote() with keep_source=False for
        core memory blocks in Letta.

        Args:
            turn_count: Current turn count for logging.

        Returns:
            True if promotion succeeded, False otherwise.
        """
        if not self._mm:
            log.warning("MemoryPromoter: no memory_manager available")
            return False

        try:
            await self._mm.promote(
                SESSION_ROLE,
                keep_source=False,
            )
            log.info(
                "MemoryPromoter: promoted to LONG_TERM at turn %d",
                turn_count,
            )
            return True
        except Exception as e:
            log.error(
                "MemoryPromoter: promote_to_longterm failed at turn %d: %s",
                turn_count, e,
            )
            return False

    async def promote_turn(self, turn_count: int) -> None:
        """Promote memory based on turn count.

        Automatically determines which tier to promote to based on
        turn count and calls the appropriate promotion method.

        Args:
            turn_count: Current turn count in conversation.
        """
        if self.should_promote_to_longterm(turn_count):
            await self.promote_to_longterm(turn_count)
        elif self.should_promote_to_episodic(turn_count):
            await self.promote_to_episodic(turn_count)
        else:
            log.debug(
                "MemoryPromoter: no promotion needed at turn %d",
                turn_count,
            )