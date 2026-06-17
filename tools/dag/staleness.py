"""Staleness check for DAG results — pure-Python MD5 verification.

A DAG writes both its result text and an MD5 hash of that text to working
memory. ``check_staleness`` reads the result back, recomputes the MD5, and
returns True when the two disagree — meaning the result has been modified
since the DAG finished (e.g. a concurrent DAG overwrote the same key, the
auto-clean task ran prematurely, or an external writer touched the key).

Used by ``tools.dag.background.collect_finished`` to flag stale results
before GOAT surfaces them, and exposed as a public function so other
callers (DAG-aware tools, the supervisor's status path) can verify a
result on demand.

Pure-Python stdlib only. No LLM. Never raises — on any error or missing
data, returns True (assume stale) so callers prefer safety over
false-confidence.
"""
from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING

from config.roles import SESSION_ROLE

if TYPE_CHECKING:
    from memory.shared import MemoryManager

log = logging.getLogger("goat2.tools.dag.staleness")

__all__ = ["check_staleness", "compute_result_hash", "STALE_PREFIX"]

# Prefix the caller prepends to a stale result so GOAT can see at a glance
# that the text it is about to surface no longer matches what the DAG
# originally produced.
STALE_PREFIX: str = "[STALE]"


def compute_result_hash(text: str) -> str:
    """Return the MD5 hex digest of ``text`` (UTF-8 encoded)."""
    return hashlib.md5((text or "").encode("utf-8")).hexdigest()


async def check_staleness(mm: "MemoryManager | None", session_id: str) -> bool:
    """True when ``dag:<sid>:result`` no longer matches the recorded MD5 hash.

    Reads both keys, recomputes the MD5 of the live result, compares to the
    stored hash. Returns True on mismatch or missing data; False when the
    result is byte-identical to what the DAG wrote. Pure-Python, never raises.
    """
    if mm is None:
        return True
    try:
        backend = getattr(getattr(mm, "working", None), "backend", None)
        if backend is None:
            return True
        result_record = await backend.get(SESSION_ROLE, f"dag:{session_id}:result")
        hash_record = await backend.get(SESSION_ROLE, f"dag:{session_id}:result_hash")
        if not result_record or not hash_record:
            return True
        live_text = (result_record.get("content") or "").strip()
        stored_hash = (hash_record.get("content") or "").strip()
        if not live_text or not stored_hash:
            return True
        live_hash = compute_result_hash(live_text)
        stale = live_hash != stored_hash
        if stale:
            log.info("check_staleness: session=%s stale (live=%s stored=%s)",
                     session_id, live_hash[:8], stored_hash[:8])
        return stale
    except Exception as exc:  # noqa: BLE001
        log.debug("check_staleness(%s): %s", session_id, exc)
        return True
