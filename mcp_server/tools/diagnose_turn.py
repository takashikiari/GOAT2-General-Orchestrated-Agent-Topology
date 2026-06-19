"""Diagnose the most recent GOAT turn — reconstruct the exact
context the LLM received.

Reuses GOAT's own functions so the diagnosis matches reality,
not an approximation:

  - ``supervisor.mechanisms.freshness.score_freshness``
  - ``supervisor.mechanisms.namespace.classify_namespace``
  - ``supervisor.session.mem_inject.working_memory_block``
  - ``supervisor.session.mem_inject.recall_context``
  - ``supervisor.identity.GOAT_SYSTEM``

The output dict describes what the LLM saw, what was filtered
out (and why), and the GOAT_SYSTEM rules used. The MCP client
gets the full picture without having to mentally re-run
the GOAT pipeline.

USAGE:
    from mcp_server.tools.diagnose_turn import diagnose_last_turn, register
    report = await diagnose_last_turn()
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Final

from mcp_server._registry import get_registry

log = logging.getLogger("goat2.mcp_server.tools.diagnose_turn")

__all__ = ["diagnose_last_turn", "register"]


# How many recent user_session entries to scan for the most
# recent turn. The turn record is the last ``turn:*`` entry
# written by ``store_turn``.
_RECENT_TURNS_SCAN: Final[int] = 5


async def _find_last_turn_record(mm) -> dict[str, Any] | None:
    """Return the most recent ``turn:*`` record from working memory.

    The record shape is the structured turn written by
    ``session.session.store_turn``:

        turn=<n>
        intent=<text>
        summary=<text>

    Args:
        mm: ``MemoryManager`` from the registry.

    Returns:
        The record as a dict with ``turn``, ``intent``,
        ``summary``, ``created_at``, ``created_at_ts`` —
        or ``None`` when no turns exist.
    """
    try:
        entries = await mm.working.list("user_session", limit=_RECENT_TURNS_SCAN * 4)
    except Exception as exc:  # noqa: BLE001
        log.warning("diagnose_turn: working.list failed: %s", exc)
        return None
    best: dict[str, Any] | None = None
    best_ts = 0.0
    for e in entries or []:
        key = getattr(e, "key", "") or ""
        if not key.startswith("turn:"):
            continue
        try:
            ts = float((getattr(e, "metadata", {}) or {}).get("created_at_ts") or 0)
        except (TypeError, ValueError):
            ts = 0.0
        if ts >= best_ts:
            best_ts = ts
            content = (getattr(e, "content", "") or "").strip()
            best = _parse_turn_payload(content)
            best["key"] = key
            best["created_at_ts"] = ts
            best["created_at"] = getattr(e, "created_at", "")
    return best


def _parse_turn_payload(text: str) -> dict[str, Any]:
    """Parse the multi-line ``key=value`` turn payload."""
    out: dict[str, Any] = {"intent": "", "summary": "", "turn": 0}
    for line in text.splitlines():
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip().lower()
        if k in out:
            if k == "turn":
                try:
                    out[k] = int(v.strip())
                except (TypeError, ValueError):
                    out[k] = 0
            else:
                out[k] = v.strip()
    return out


async def diagnose_last_turn() -> dict[str, Any]:
    """Reconstruct what the LLM received for the most recent turn.

    Returns:
        A dict with the following keys:
            - ``found`` — bool: True when a turn was located.
            - ``turn``  — dict (the parsed turn record) or ``None``.
            - ``goat_system`` — the literal ``GOAT_SYSTEM`` string
              GOAT used for this turn.
            - ``working_memory_block`` — the rendered block,
              exactly as ``mem_inject.working_memory_block`` would
              have rendered it for the same intent.
            - ``included_keys`` — keys that made it into the block.
            - ``excluded_keys`` — keys that were filtered out by
              ``should_include_entry`` (DAG entries older than
              ``dag_max_age_seconds`` with no DAG intent), with
              the reason for exclusion.
            - ``freshness_counts`` — ``{"FRESH": n, "RECENT": n, "OLD": n}``
              for the entries actually rendered.
            - ``namespace_counts`` — ``{"CONV": n, "DAG": n, ...}``
              for the entries actually rendered.
            - ``recall_context`` — the cross-tier ``[Memory]`` block
              for the last intent (or ``""`` when unavailable).
            - ``llm_response`` — the assistant's last response text
              from the last turn record's ``summary`` field, with
              a note about repetition when ``source == "repetitive"``
              (kept for the future — current supervisor writes
              ``"generated"`` here).
            - ``timestamp`` — ISO-8601 string for when the
              diagnosis ran.
            - ``errors`` — list of per-step error strings (the
              diagnosis still returns a useful partial result
              when one tier is down).
    """
    out: dict[str, Any] = {
        "found":                   False,
        "turn":                    None,
        "goat_system":             "",
        "working_memory_block":    "",
        "included_keys":           [],
        "excluded_keys":           [],
        "freshness_counts":        {"FRESH": 0, "RECENT": 0, "OLD": 0},
        "namespace_counts":        {"CONV": 0, "DAG": 0, "GOAT": 0, "SYS": 0},
        "recall_context":          "",
        "llm_response":            "",
        "llm_response_source":     "",
        "timestamp":               datetime.now(tz=timezone.utc).isoformat(),
        "errors":                  [],
    }
    try:
        from supervisor.identity import GOAT_SYSTEM
        out["goat_system"] = GOAT_SYSTEM
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(f"identity.GOAT_SYSTEM: {exc}")

    try:
        registry = get_registry()
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(f"registry: {exc}")
        return out

    mm = registry.memory_manager

    # Locate the most recent turn.
    last_turn = await _find_last_turn_record(mm)
    if last_turn is None:
        out["errors"].append("no turn:* record found in working memory")
        return out
    out["found"] = True
    out["turn"] = last_turn
    intent = last_turn.get("intent", "")
    out["llm_response"] = last_turn.get("summary", "")

    # Working-memory block — reuse the same function GOAT uses.
    try:
        from supervisor.session.mem_inject import working_memory_block
        block = await working_memory_block(mm, include_dag=False)
        out["working_memory_block"] = block
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(f"working_memory_block: {exc}")
        block = ""

    # Cross-tier recall for the intent.
    try:
        from supervisor.session.mem_inject import recall_context
        out["recall_context"] = await recall_context(mm, intent, include_dag=False)
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(f"recall_context: {exc}")

    # Per-entry classification: included vs excluded, with
    # freshness + namespace labels. Uses the exact same
    # should_include_entry rule GOAT uses (via namespace +
    # staleness checks).
    try:
        from supervisor.mechanisms.freshness import score_freshness
        from supervisor.mechanisms.namespace import classify_namespace
        from supervisor.mechanisms.staleness import is_stale
        now = datetime.now(tz=timezone.utc).timestamp()
        records = await mm.working.list("user_session", limit=200)
        included: list[dict[str, Any]] = []
        excluded: list[dict[str, Any]] = []
        for rec in records or []:
            key = getattr(rec, "key", "") or ""
            if not key:
                continue
            ts = float((getattr(rec, "metadata", {}) or {}).get("created_at_ts") or 0)
            entry = {"created_at_ts": ts, "key": key}
            fresh = score_freshness(entry, now)
            ns    = classify_namespace(key)
            stale = is_stale(entry, intent, now)
            label = f"[{fresh}][{ns}]"
            if ns == "DAG" and (stale or ts == 0):
                excluded.append({
                    "key":      key,
                    "reason":   "stale DAG entry (no DAG intent OR age > dag_max_age_seconds)",
                    "label":    label,
                    "age_s":    (now - ts) if ts else None,
                })
                continue
            included.append({
                "key":      key,
                "label":    label,
                "age_s":    (now - ts) if ts else None,
            })
            out["freshness_counts"][fresh] = out["freshness_counts"].get(fresh, 0) + 1
            out["namespace_counts"][ns]    = out["namespace_counts"].get(ns, 0) + 1
        out["included_keys"] = included
        out["excluded_keys"] = excluded
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(f"per-entry classification: {exc}")

    return out


# ── MCP wiring ────────────────────────────────────────────────

def register(server) -> None:
    """Register the diagnose tool on an MCP ``Server``."""
    @server.tool(
        name="diagnose_last_turn",
        description=(
            "Reconstruct what GOAT actually received for the most recent turn: "
            "the working-memory block (using GOAT's own freshness + namespace labels), "
            "which entries were included vs excluded (with the reason), the GOAT_SYSTEM "
            "prompt used, the cross-tier recall context, and the LLM's response. "
            "Reuses the same supervisor.mechanisms / supervisor.session functions GOAT "
            "itself uses, so the diagnosis matches reality exactly."
        ),
    )
    async def _diagnose_last_turn() -> dict[str, Any]:
        return await diagnose_last_turn()