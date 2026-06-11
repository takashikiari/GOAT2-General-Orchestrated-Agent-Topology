"""Behavioral learning — semantic storage of user corrections.

GOAT 2.0 learns **semantically** from the user's corrections:

  1. When a user disagrees with GOAT's routing, the supervisor
     detects the disagreement via the LLM (no keywords).
  2. The correction is written to **episodic memory** as a labeled
     example: original intent, what GOAT did, what the user wanted.
  3. On the next similar intent, the classifier retrieves nearby
     corrections from episodic memory and shows them to the LLM as
     soft hints.

There are zero hardcoded examples, zero regex rules, and zero
"if the user said X, do Y" patterns. The user profile in long-term
memory is a semantic summary, not an attribute list.
"""
from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING

log = logging.getLogger("goat2.supervisor.classification.behavioral_learning")

if TYPE_CHECKING:
    from config.registry import ServiceRegistry

__all__ = ["store_correction", "recall_corrections"]


async def store_correction(
    registry: "ServiceRegistry",
    intent: str,
    goat_routed: str,
    user_wanted: str,
    note: str = "",
) -> bool:
    """Persist a user correction to episodic memory (ChromaDB).

    Args:
        registry: The ServiceRegistry holding the memory manager.
        intent: The user's original message text.
        goat_routed: What GOAT decided ("conversational" / "analytical"
                     / "complex").
        user_wanted: What the user said they wanted (free text).
        note: Optional additional context (e.g. the user's wording).

    Returns:
        True if the correction was stored, False on any error.
    """
    mm = getattr(registry, "memory_manager", None)
    if mm is None:
        return False
    try:
        payload = {
            "type": "user_correction",
            "intent": intent[:500],
            "goat_routed": goat_routed,
            "user_wanted": user_wanted[:500],
            "note": note[:300],
            "ts": time.time(),
        }
        # Write into episodic tier — supervisor-only. Uses
        # `add` if available, otherwise the closest store call.
        if hasattr(mm, "episodic") and hasattr(mm.episodic, "add"):
            await mm.episodic.add(
                doc_id=f"correction:{int(time.time() * 1000)}",
                content=json.dumps(payload, ensure_ascii=False),
                metadata={"type": "user_correction", "goat_routed": goat_routed},
            )
            log.info("correction stored in episodic: goat=%s wanted=%s",
                     goat_routed, user_wanted[:60])
            return True
        # Fallback: working memory (less ideal but still supervisor-owned)
        from config.roles import SESSION_ROLE
        from config.limits import INFERRED_MEMORY_TTL
        key = f"goat_correction:{int(time.time() * 1000)}"
        now = time.time()
        record = {
            "id": key,
            "agent_role": SESSION_ROLE,
            "key": key,
            "content": json.dumps(payload, ensure_ascii=False),
            "metadata": {"type": "user_correction", "session_id": "goat"},
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            "created_at_ts": now,
            "expires_at": now + INFERRED_MEMORY_TTL,
        }
        await mm.working.backend.set(
            SESSION_ROLE, key, record, expires_at=record["expires_at"]
        )
        return True
    except Exception as e:
        log.warning("store_correction failed: %s", e)
        return False


async def recall_corrections(registry: "ServiceRegistry", limit: int = 3) -> list[str]:
    """Recall recent user corrections as short hint strings.

    Returns up to `limit` short human-readable strings, each
    summarizing one prior correction. The classifier shows these
    to the LLM as soft hints.
    """
    mm = getattr(registry, "memory_manager", None)
    if mm is None:
        return []
    try:
        if hasattr(mm, "episodic") and hasattr(mm.episodic, "search"):
            results = await mm.episodic.search(
                "user correction routing preference", limit=limit
            )
        else:
            return []
        hints: list[str] = []
        for r in results or []:
            doc = r.get("content") if isinstance(r, dict) else None
            if not doc:
                continue
            try:
                payload = json.loads(doc) if isinstance(doc, str) else doc
            except Exception:
                payload = None
            if isinstance(payload, dict):
                intent = payload.get("intent", "?")[:80]
                goat = payload.get("goat_routed", "?")
                wanted = payload.get("user_wanted", "?")[:80]
                hints.append(
                    f"intent=\"{intent}\" → goat={goat}, user wanted: {wanted}"
                )
            elif isinstance(doc, str):
                hints.append(doc[:200])
        return hints
    except Exception as e:
        log.debug("recall_corrections failed: %s", e)
        return []
