"""Context gatherers for the pure LLM intent classifier.

These functions read from working memory, long-term memory, and
episodic memory to assemble the context the classifier LLM needs.
Every function is best-effort: on any error it returns an empty
result and logs at DEBUG level. None of them match keywords or
apply hardcoded rules.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from utils.llm_utils import _call_llm

log = logging.getLogger("goat2.supervisor.classification.classifier_context")

if TYPE_CHECKING:
    from config.registry import Registry

__all__ = [
    "detect_override",
    "gather_active_dags",
    "gather_user_profile",
    "gather_hints",
]


async def detect_override(intent: str, registry: "Registry") -> str | None:
    """Ask the LLM whether the user is explicitly overriding routing.

    Returns "conversational", "complex", or None. The LLM is told
    in prose what an override looks like. No keywords are listed.
    """
    system = (
        "Decide whether the user is explicitly asking for a specific "
        "routing mode. If the user wants GOAT to answer directly without "
        "spawning the deep-thinking pipeline, reply: conversational. "
        "If the user wants GOAT to think deeply, spawn the DAG, or run "
        "the full pipeline, reply: complex. If there is no explicit "
        "routing request, reply: none. Reply with exactly one word: "
        "conversational, complex, or none."
    )
    try:
        raw = await _call_llm(
            registry.settings.agents.get("memory"),
            [
                {"role": "system", "content": system},
                {"role": "user", "content": intent},
            ],
        )
    except Exception as e:
        log.debug("override detection failed: %s", e)
        return None
    token = raw.strip().lower().split()[0] if raw.strip() else ""
    if token in ("conversational", "complex"):
        return token
    return None


async def gather_active_dags(registry: "Registry") -> list[dict]:
    """Read working memory for active DAG sessions.

    Returns a list of dicts: {session_id, wave, total_waves, status}.
    Best-effort: returns [] on any backend error. Delegates to the
    supervisor's DAG-awareness module so there is one canonical
    read primitive (no duplication of the scan logic).
    """
    try:
        from supervisor.pipeline.dag_awareness import scan_active_dags
        return await scan_active_dags(registry)
    except Exception as e:
        log.debug("gather_active_dags failed: %s", e)
        return []


async def gather_user_profile(registry: "Registry") -> str:
    """Read the user profile from long-term memory (semantic summary)."""
    if not getattr(registry, "memory_manager", None):
        return "(no profile available)"
    try:
        from supervisor.identity import load_user_profile
        return (await load_user_profile(registry.memory_manager)) or "(empty profile)"
    except Exception as e:
        log.debug("profile load failed: %s", e)
        return "(profile unavailable)"


async def gather_hints(registry: "Registry") -> list[str]:
    """Read prior user corrections from episodic memory (semantic).

    Best-effort semantic search. Returns up to 3 short hints. The
    exact query is a generic "user correction" probe; the resulting
    list is shown to the LLM as soft context, not as a hard rule.
    Delegates to the behavioral-learning module so the classifier
    and the supervisor share one recall path.
    """
    try:
        from supervisor.pipeline.behavioral_learning import recall_corrections
        return await recall_corrections(registry, limit=3)
    except Exception as e:
        log.debug("gather_hints failed: %s", e)
        return []
