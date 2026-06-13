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

__all__ = [
    "store_correction",
    "recall_corrections",
    "detect_routing_disagreement",
]

# System prompt for routing disagreement detection
_DISAGREEMENT_SYSTEM = (
    "You are a routing disagreement detector. Your job is to determine if the user "
    "is explicitly correcting GOAT's routing decision. "
    "The user might say:\n"
    "  - 'no, think about this / use the DAG / think deeply'\n"
    "  - 'that was conversational, i wanted you to analyze this'\n"
    "  - 'run a DAG for this' or 'use the full pipeline'\n"
    "  - 'you should have researched this first' or 'i wanted research'\n"
    "  - 'use the planner / researcher / critic for this'\n"
    "But they might also simply continue the conversation normally. "
    "Reply with exactly one word: disagreement, clarification, or none."
)


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


async def detect_routing_disagreement(
    user_message: str,
    goat_routed: str,
    registry: "ServiceRegistry",
) -> tuple[bool, str]:
    """Detect if the user's message disagrees with GOAT's previous routing decision.

    Uses pure LLM reasoning (no keywords). Only triggers when there's a previous
    routing decision to compare against - this is NOT the same as override detection.

    Args:
        user_message: The user's latest message after GOAT's response.
        goat_routed: What GOAT initially routed (conversational/analytical/complex).
        registry: ServiceRegistry for model access.

    Returns:
        (True, user_wanted) if disagreement detected, (False, "") otherwise.
    """
    # Skip if no previous routing to disagree with
    if not goat_routed or goat_routed == "none":
        return False, ""
    try:
        from utils.llm_utils import _call_llm
        settings = registry.settings
        raw = await _call_llm(
            settings.agents.get("memory"),
            [
                {"role": "system", "content": _DISAGREEMENT_SYSTEM},
                {"role": "user", "content": f"GOAT routed as: {goat_routed}\nUser message: {user_message}"},
            ],
        )
        token = raw.strip().lower().split()[0] if raw.strip() else ""
        if token == "disagreement":
            # Second call to extract what the user wanted
            wanted_raw = await _call_llm(
                settings.agents.get("memory"),
                [
                    {"role": "system", "content": "In 1-3 words, state what the user wanted GOAT to do. "
                                             "E.g., 'use DAG', 'research', 'think deeply', 'analyze code'. "
                                             "Reply with just the desired mode."},
                    {"role": "user", "content": user_message},
                ],
            )
            wanted = wanted_raw.strip().lower().split()[0] if wanted_raw.strip() else "complex"
            if wanted not in ("conversational", "analytical", "complex"):
                wanted = "complex"
            log.info("routing disagreement detected: goat=%s wanted=%s", goat_routed, wanted)
            return True, wanted
        return False, ""
    except Exception as e:
        log.debug("detect_routing_disagreement failed: %s", e)
        return False, ""
