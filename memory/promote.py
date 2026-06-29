"""memory.promote — episodic → permanent promotion into the Letta L1 facts block.

GOAT lifts a *stable, reusable* fact from the conversation/episodic layer into
permanent core-memory (L1). Gated rather than a free append, to keep L1 small
and curated — mirroring how Letta core-memory is meant to be managed:

  - **Upsert by key** — `promote_memory(key, value)` sets `facts[key] = value`,
    so re-promoting an existing key updates instead of duplicating. L1 grows only
    when GOAT adds a *new* key.
  - **Token cap** — promotion is refused once the formatted facts block would
    exceed ``L1_FACTS_MAX_TOKENS``, nudging GOAT to retire/shorten a fact first.
    L1 is mandatory (off the top of every turn's budget), so it must stay lean.

GOAT invokes this via the ``promote_memory`` tool (no background daemon). The
``store_memory`` tool writes L3 (episodic, recency-bounded, grows freely);
``promote_memory`` writes L1 (permanent, small, curated) — two distinct tiers.
Letta *archival* memory (richer history, via ``PermanentMemory.archive_entries``)
is a separate, larger store left for a follow-up tool.
"""
from __future__ import annotations

from memory.budget import estimate_tokens
from memory.config import L1_FACTS_MAX_TOKENS
from utils.logging.setup import get_logger

log = get_logger(__name__)

__all__ = ["promote_fact"]


def _format_facts(facts: dict[str, str]) -> str:
    """Format L1 facts as ``- key: value`` lines (mirrors ``MemoryLayers._format_facts``)."""
    return "\n".join(f"- {k}: {v}" for k, v in facts.items())


async def promote_fact(permanent, key: str, value: str) -> str:
    """Upsert ``key=value`` into the Letta L1 facts block, cap-guarded. Returns a status string.

    Args:
        permanent: ``PermanentMemory`` instance (talks to Letta core-memory).
        key: Fact key (e.g. ``"user_name"``). Must be non-empty.
        value: Fact value. Empty value is allowed (records an empty fact).

    Returns:
        A ``✅`` confirmation, or a ``❌`` reason (empty key / L1 cap exceeded /
        Letta failure) — never raises; the tool surfaces the string to the model.
    """
    if not key:
        return "❌ promote_memory requires a non-empty key."
    try:
        facts = await permanent.get_all_facts()
        facts[key] = value                       # simulate the upsert for the cap check
        if estimate_tokens(_format_facts(facts)) > L1_FACTS_MAX_TOKENS:
            return (
                f"❌ L1 facts block is full (cap {L1_FACTS_MAX_TOKENS} tokens). "
                f"Retire or shorten an existing fact before promoting {key!r}."
            )
        await permanent.store_fact(key, value)
        log.info("L1 promote ok: key=%r", key)
    except Exception as exc:  # noqa: BLE001 — surface to the model, don't crash the turn
        return f"❌ promote_memory failed (Letta unavailable): {exc}"
    return f"✅ Promoted to permanent core-memory: {key} = {value[:80]}"