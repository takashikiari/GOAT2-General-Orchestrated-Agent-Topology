"""Higher-level state query tools — the seven inspectors that
turn a wall of log lines into a structured diagnosis.

READ-ONLY: every function here only reads from the existing
memory tiers or the rotating log file. No writes, no locks.
Safe to run alongside ``telegram_bot.py``.

THE SEVEN TOOLS:

  - ``search_logs(pattern, level="ALL", minutes=60, limit=100)``
      Grep-style search across the recent log window. Regex
      via ``re.search``. Wraps ``query_logs`` primitives so
      the same level-filter / time-window semantics apply.

  - ``get_session_trace(session_id, last_n_turns=10)``
      Reconstruct the last N turns from persisted
      ``turn:*`` records in working memory. Each turn
      includes ``intent``, ``summary``, ``called_tools``,
      ``source``, and timestamps. Useful for inspecting
      "what did the model say on each turn of the loop?"
      without grepping the log.

  - ``get_supervisor_state(session_id=None)``
      Live-ish supervisor state. When ``session_id`` is
      None, returns the MOST RECENT supervisor that wrote
      a turn. Pulls ``_history.length``, last action,
      last tool calls, and any per-turn metadata. The
      actual live ``GoatSupervisor`` instance lives in
      the Telegram bot's process and is not visible from
      the MCP server; we reconstruct a faithful snapshot
      from the working-memory records that supervisor
      wrote.

  - ``get_memory_entry(key, tier="working")``
      Fetch a single memory entry by exact key, return
      full content + metadata + freshness. Wraps
      ``MemoryManager.working.get`` /
      ``MemoryManager.episodic.get`` /
      ``MemoryManager.long_term.get`` so callers can
      inspect one entry without listing the whole tier.

  - ``list_dangling_dag_entries(tier="episodic", limit=100)``
      Return episodic entries that look "stale" — older
      than ``dag_max_age_seconds`` from
      ``config/memory.toml [freshness]`` — OR that carry
      an explicit ``dag_status: completed`` / ``stale: True``
      marker in their metadata. These are exactly the
      entries the model keeps finding in search and that
      pollute recall ("yes there is a DAG note from 13h
      ago — what do you want to do with it?"). Returns
      them sorted by age (oldest first), with a per-entry
      ``staleness_reason`` so the operator can decide
      whether to delete them or just hide them.

  - ``get_current_system_prompt(include_style=True)``
      Return the literal ``GOAT_SYSTEM`` prompt exactly as
      ``pipeline.prompt_helpers.build_system_prompt`` would
      compose it RIGHT NOW (with the style directive if
      one is configured). Useful for prompt-engineering
      debugging — no need to mentally re-run the prompt
      builder to confirm which rules are active.

  - ``get_recent_tool_calls(session_id="", last_n_turns=5)``
      Return a chronological, flat list of every tool
      call across the last N turns of a session. Each row
      has ``turn``, ``tool``, ``ok``, ``summary`` (one-line
      result preview). Sourced from the structured
      ``turn:<n>:actions`` records the supervisor writes
      via ``store_action_log``. The fast path to answer
      "did the model call memory_delete or did it just
      talk about it?" — no grep needed.

All seven tools accept a ``role`` parameter where the
underlying layer takes one (default ``"user_session"``,
matching the value ``telegram_bot`` writes under).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Final

from mcp_server._registry import get_registry
from mcp_server.tools.query_logs import _LOG_PATH, _parse_log_timestamp, _read_window

log = logging.getLogger("goat2.mcp_server.tools.query_state")

__all__ = [
    "search_logs",
    "get_session_trace",
    "get_supervisor_state",
    "get_memory_entry",
    "list_dangling_dag_entries",
    "get_current_system_prompt",
    "get_recent_tool_calls",
    "register",
]


# ── Tier role for MemoryManager calls ──────────────────────────────────────
# The Telegram bot writes user-session entries under this role.
# See ``mcp_server/tools/query_memory.py`` for the same constant.
_DEFAULT_ROLE: Final[str] = "user_session"

# Bound the per-tool output size so a runaway log window or
# memory entry doesn't blow the MCP response size limit.
_MAX_LOG_HITS: Final[int] = 500
_MAX_TRACE_TURNS: Final[int] = 100
_MAX_CONTENT_CHARS: Final[int] = 8_000  # ~8KB per entry content preview

# Recognized tier names for get_memory_entry / get_session_trace.
_VALID_TIERS: Final[tuple[str, ...]] = ("working", "episodic", "long_term")


# ════════════════════════════════════════════════════════════════════════════
# 1. search_logs
# ════════════════════════════════════════════════════════════════════════════


def search_logs(
    pattern: str,
    level: str = "ALL",
    minutes: int = 60,
    limit: int = 100,
) -> dict[str, Any]:
    """Grep-style search across the recent log window.

    Args:
        pattern: A regex (Python ``re.search`` semantics) or
            plain substring to search for. Empty pattern
            matches every line within the time window.
        level: One of ``"ALL"``, ``"DEBUG"``, ``"INFO"``,
            ``"WARNING"``, ``"ERROR"``, ``"CRITICAL"``.
        minutes: Time window in minutes (default 60).
        limit: Cap the number of matches returned (default
            100, hard-capped at ``_MAX_LOG_HITS``).

    Returns:
        A dict with:
            - ``matches``: list of matching log lines (most
              recent last), truncated to ``limit``.
            - ``match_count``: total matches BEFORE truncation.
            - ``truncated``: True when ``match_count > limit``.
            - ``pattern``: echo of the input pattern.
            - ``minutes``, ``level``: echo of input args.
            - ``errors``: list of error strings (e.g. invalid
              regex, missing log file).

    Notes:
        Regex errors are caught and reported in ``errors``
        — the tool never raises on a bad pattern. An invalid
        regex falls back to substring search.
    """
    out: dict[str, Any] = {
        "matches":     [],
        "match_count": 0,
        "truncated":   False,
        "pattern":     pattern,
        "minutes":     int(minutes),
        "level":       level,
        "errors":      [],
    }
    if not _LOG_PATH.exists():
        out["errors"].append(f"log file not found: {_LOG_PATH}")
        return out
    cap = max(1, min(int(limit or 100), _MAX_LOG_HITS))

    # Compile regex; fall back to substring on failure so a
    # bad pattern from the MCP client doesn't kill the tool.
    rx: re.Pattern[str] | None = None
    if pattern:
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            out["errors"].append(f"invalid regex: {exc} — falling back to substring")
            rx = None

    from datetime import timedelta
    cutoff = datetime.now(tz=timezone.utc) - timedelta(minutes=max(0, int(minutes)))
    needle_lower = pattern.lower() if not rx else None

    # Inline read (rather than reusing ``_read_window``) because
    # we need both the level filter AND the regex/needle filter
    # together. The level filter is a substring match on the
    # literal ``" LEVEL "`` token — same convention as
    # ``_level_matches`` in ``query_logs``.
    lvl = (level or "ALL").upper()
    level_token = None if lvl == "ALL" else " " + lvl + " "

    matched: list[str] = []
    try:
        with _LOG_PATH.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                ts = _parse_log_timestamp(line)
                if ts is not None and ts < cutoff:
                    continue
                if level_token and level_token not in line.upper():
                    continue
                if pattern:
                    if rx is not None:
                        if not rx.search(line):
                            continue
                    else:
                        if needle_lower not in line.lower():
                            continue
                matched.append(line.rstrip("\n"))
    except OSError as exc:
        out["errors"].append(f"read failed: {exc}")
        return out

    out["match_count"] = len(matched)
    if len(matched) > cap:
        out["truncated"] = True
        matched = matched[-cap:]
    out["matches"] = matched
    return out


# ════════════════════════════════════════════════════════════════════════════
# 2. get_session_trace
# ════════════════════════════════════════════════════════════════════════════


def _parse_turn_payload(text: str) -> dict[str, Any]:
    """Parse the multi-line ``key=value`` turn payload.

    Each turn record is written by ``store_turn`` as::

        turn=<n>
        intent=<text>
        summary=<text>

    The action log appended by ``store_action_log`` adds::

        action=<direct|clarify|dag>
        called_tools=[a, b, c]
        source=<generated|memory|repetitive|...>

    All key=value lines are folded into the returned dict.
    List values (e.g. ``called_tools``) are split on ``,``
    and stripped; repeated keys overwrite (last-write-wins
    in the dict — fine for trace purposes).
    """
    out: dict[str, Any] = {}
    for line in (text or "").splitlines():
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip().lower()
        v = v.strip()
        if k == "turn":
            try:
                out["turn"] = int(v)
            except (TypeError, ValueError):
                pass
        elif k == "called_tools":
            # Strip the surrounding brackets the writer adds
            # (e.g. ``called_tools=[memory_search, memory_get]``).
            v = v.strip("[]")
            out["called_tools"] = [t.strip() for t in v.split(",") if t.strip()]
        elif k in ("intent", "summary", "action", "source"):
            out[k] = v
        else:
            # Unknown keys — keep so future fields surface
            # without code changes (e.g. dag_session_id).
            out.setdefault(k, v)
    return out


async def _list_turn_records(mm, tier: str, role: str, limit: int) -> list:
    """Return the most recent ``turn:*`` records from a tier.

    Falls back to episodic when working has none (the bot
    promotes turns to episodic on session end via
    ``store_and_promote``).
    """
    layer = getattr(mm, tier, None)
    if layer is None:
        return []
    try:
        # Overfetch because the tier contains other roles
        # (e.g. system memory) — we filter to turn: keys below.
        raw = await layer.list(role, limit=max(limit * 4, limit))
    except Exception as exc:  # noqa: BLE001
        log.warning("get_session_trace: %s.list failed: %s", tier, exc)
        return []
    return [e for e in (raw or []) if (getattr(e, "key", "") or "").startswith("turn:")]


async def _get_entry(mm, tier: str, role: str, key: str):
    """Fetch one entry from a memory tier — robust to backend shape.

    ``WorkingMemoryLayer`` exposes ``list`` but not ``get``
    directly — the actual ``get`` lives on the backend. The
    episodic and long_term layers have their own ``get``
    method. We probe in this order:

      1. ``layer.backend.get(role, key)`` — works for working
         (where ``layer.get`` doesn't exist) and for any
         backend that exposes it directly.
      2. ``layer.get(role, key)`` — works for episodic /
         long_term when they expose ``get`` at the layer
         level (the common case).
      3. Scan the list — O(n) fallback that always works
         because every tier exposes ``list``.

    Returns the entry, or ``None`` when not found.
    """
    layer = getattr(mm, tier, None)
    if layer is None:
        return None
    # Path 1: ``layer.backend.get`` — the working layer's
    # real path. Check this first so we don't fall into a
    # stub ``MagicMock().get`` that would return a fake value.
    backend = getattr(layer, "backend", None)
    if backend is not None:
        get_fn = getattr(backend, "get", None)
        if callable(get_fn):
            try:
                return await get_fn(role, key)
            except Exception as exc:  # noqa: BLE001
                log.debug("_get_entry: %s.backend.get failed: %s", tier, exc)
    # Path 2: ``layer.get`` — episodic / long_term directly.
    get_fn = getattr(layer, "get", None)
    if callable(get_fn):
        try:
            return await get_fn(role, key)
        except Exception as exc:  # noqa: BLE001
            log.debug("_get_entry: %s.get failed: %s", tier, exc)
    # Path 3: scan the list. Always available.
    list_fn = getattr(layer, "list", None)
    if callable(list_fn):
        try:
            entries = await list_fn(role, limit=500)
        except Exception:  # noqa: BLE001
            return None
        for e in entries or []:
            if getattr(e, "key", "") == key:
                return e
    return None


async def get_session_trace(
    session_id: str,
    last_n_turns: int = 10,
    tier: str = "working",
) -> dict[str, Any]:
    """Return the last N turns of a session as a structured trace.

    Args:
        session_id: The supervisor ``session_id`` (UUID4) to
            trace. Only turns with metadata ``session_id``
            equal to this value are returned. Pass an
            empty string to return ALL recent turns across
            sessions (useful when session_id is unknown).
        last_n_turns: How many of the most recent matching
            turns to include (default 10, hard-capped at
            ``_MAX_TRACE_TURNS``).
        tier: Where to look. ``"working"`` (default) holds
            the current session's turns; ``"episodic"``
            holds promoted turns from past sessions.

    Returns:
        A dict with:
            - ``found`` — bool
            - ``turns`` — list of dicts (most recent last),
              each with ``turn``, ``intent``, ``summary``,
              ``action``, ``called_tools``, ``source``,
              ``created_at``, ``created_at_ts``, ``key``.
            - ``session_id`` — echo of input
            - ``tier``, ``last_n_turns`` — echo of input
            - ``total_found`` — total turns matching
              ``session_id`` BEFORE truncation to ``last_n_turns``
            - ``errors`` — per-step error strings

    Notes:
        The MCP server is a SEPARATE process from the
        Telegram bot, so it cannot see the live
        ``GoatSupervisor`` instance. Instead we read the
        ``turn:*`` records the supervisor wrote to working
        memory. Each record carries the session_id in its
        metadata, which is how we filter.
    """
    tier_lc = (tier or "working").lower()
    if tier_lc not in _VALID_TIERS:
        return {
            "found": False, "turns": [],
            "session_id": session_id, "tier": tier,
            "last_n_turns": last_n_turns, "total_found": 0,
            "errors": [f"unknown tier {tier!r}; expected one of {_VALID_TIERS}"],
        }
    cap = max(1, min(int(last_n_turns or 10), _MAX_TRACE_TURNS))
    out: dict[str, Any] = {
        "found":         False,
        "turns":         [],
        "session_id":    session_id,
        "tier":          tier_lc,
        "last_n_turns":  cap,
        "total_found":   0,
        "errors":        [],
    }
    try:
        registry = get_registry()
        mm = registry.memory_manager
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(f"registry: {exc}")
        return out

    records = await _list_turn_records(mm, tier_lc, _DEFAULT_ROLE, cap)
    if not records:
        out["errors"].append(f"no turn:* records in tier {tier_lc!r}")
        return out

    # Filter to session_id. We accept empty session_id as
    # "any session" so the operator can list turns even
    # when the bot never recorded one.
    turns: list[dict[str, Any]] = []
    for rec in records:
        meta = getattr(rec, "metadata", {}) or {}
        rec_session = str(meta.get("session_id") or "")
        if session_id and rec_session != session_id:
            continue
        content = (getattr(rec, "content", "") or "").strip()
        parsed = _parse_turn_payload(content)
        try:
            ts = float(meta.get("created_at_ts") or 0)
        except (TypeError, ValueError):
            ts = 0.0
        turns.append({
            "key":           getattr(rec, "key", ""),
            "turn":          parsed.get("turn", 0),
            "intent":        parsed.get("intent", ""),
            "summary":       parsed.get("summary", ""),
            "action":        parsed.get("action", ""),
            "called_tools":  parsed.get("called_tools", []),
            "source":        parsed.get("source", ""),
            "session_id":    rec_session,
            "created_at":    getattr(rec, "created_at", ""),
            "created_at_ts": ts,
        })
    # Sort by turn number (most recent last) — the underlying
    # list() may not be strictly ordered.
    turns.sort(key=lambda t: t.get("turn") or 0)
    out["total_found"] = len(turns)
    if turns:
        out["found"] = True
    if len(turns) > cap:
        turns = turns[-cap:]
    out["turns"] = turns
    return out


# ════════════════════════════════════════════════════════════════════════════
# 3. get_supervisor_state
# ════════════════════════════════════════════════════════════════════════════


async def get_supervisor_state(
    session_id: str | None = None,
) -> dict[str, Any]:
    """Return the live-ish state of a supervisor (or the most recent one).

    The MCP server runs in a SEPARATE PROCESS from the
    Telegram bot, so it cannot inspect the live
    ``GoatSupervisor`` instance in the bot's memory. We
    reconstruct a faithful snapshot from the ``turn:*``
    records that supervisor wrote to working memory, plus
    the ``_last_turn_result`` summary fields.

    Args:
        session_id: Optional. When None (default), picks
            the most recent session_id that has a turn
            record in working memory. Pass an explicit
            UUID to inspect a specific session.

    Returns:
        A dict with:
            - ``found`` — bool
            - ``session_id`` — the resolved session id
            - ``history_length`` — number of committed
              turns in this session (i.e. turn count
              EXCLUDING the current pending user turn).
            - ``last_turn`` — the most recent turn dict
              (same shape as ``get_session_trace`` rows).
            - ``last_action`` — ``"direct" | "clarify" | "dag"``
              from the last turn.
            - ``last_source`` — ``"generated" | "memory" | "repetitive" | ...``
            - ``last_called_tools`` — list of tool names
              from the last turn.
            - ``errors`` — list of error strings.
    """
    out: dict[str, Any] = {
        "found":              False,
        "session_id":         session_id or "",
        "history_length":     0,
        "last_turn":          None,
        "last_action":        "",
        "last_source":        "",
        "last_called_tools":  [],
        "errors":             [],
    }
    try:
        registry = get_registry()
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(f"registry: {exc}")
        return out

    # Locate the most recent turn record(s) so we can
    # discover the session_id (when none was supplied)
    # and pull the last_turn summary.
    mm = registry.memory_manager
    try:
        records = await _list_turn_records(mm, "working", _DEFAULT_ROLE, 50)
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(f"working.list: {exc}")
        return out

    if not records:
        out["errors"].append("no turn:* records in working memory")
        return out

    # Resolve the target session_id. When the caller didn't
    # supply one, fall back to the most recent record's
    # session_id so the operator can do
    # ``get_supervisor_state()`` with no args and still
    # get the most useful answer.
    #
    # NOTE: turn records in working memory do NOT currently
    # carry the session_id in their metadata (the supervisor
    # writes it only on the action-log records). When the
    # records have no session_id at all, we treat them as
    # belonging to the most recent supervisor — the operator
    # gets the snapshot of the live conversation, which is
    # what they usually want.
    if not session_id:
        latest = max(
            records,
            key=lambda r: float((getattr(r, "metadata", {}) or {}).get("created_at_ts") or 0),
        )
        session_id = str((getattr(latest, "metadata", {}) or {}).get("session_id") or "")
        out["session_id"] = session_id
    # If the records don't carry session_id, fall back to
    # treating ALL records as belonging to the requested
    # session — the live session is the only one in working
    # memory at a time.
    session_in_meta = any(
        (getattr(r, "metadata", {}) or {}).get("session_id")
        for r in records
    )

    # Filter to this session. Sort by turn number. When the
    # records don't carry session_id in metadata (the current
    # state of turn_persistence), include all of them —
    # working memory only holds the live session anyway.
    session_turns: list[dict[str, Any]] = []
    for rec in records:
        meta = getattr(rec, "metadata", {}) or {}
        rec_session = str(meta.get("session_id") or "")
        if session_in_meta and rec_session != session_id:
            continue
        content = (getattr(rec, "content", "") or "").strip()
        parsed = _parse_turn_payload(content)
        try:
            ts = float(meta.get("created_at_ts") or 0)
        except (TypeError, ValueError):
            ts = 0.0
        session_turns.append({
            "key":           getattr(rec, "key", ""),
            "turn":          parsed.get("turn", 0),
            "intent":        parsed.get("intent", ""),
            "summary":       parsed.get("summary", ""),
            "action":        parsed.get("action", ""),
            "called_tools":  parsed.get("called_tools", []),
            "source":        parsed.get("source", ""),
            "created_at":    getattr(rec, "created_at", ""),
            "created_at_ts": ts,
        })
    if not session_turns:
        out["errors"].append(f"no turns for session_id={session_id}")
        return out
    session_turns.sort(key=lambda t: t.get("turn") or 0)
    out["history_length"] = len(session_turns)
    last = session_turns[-1]
    out["last_turn"] = last
    out["last_action"] = last.get("action", "") or ""
    out["last_source"] = last.get("source", "") or ""
    out["last_called_tools"] = list(last.get("called_tools", []) or [])
    out["found"] = True
    return out


# ════════════════════════════════════════════════════════════════════════════
# 4. get_memory_entry
# ════════════════════════════════════════════════════════════════════════════


async def get_memory_entry(key: str, tier: str = "working") -> dict[str, Any]:
    """Fetch a single memory entry by exact key.

    Args:
        key: The exact key string (e.g. ``"turn:5:intent"``
            or ``"dag:note:abc-123"``).
        tier: ``"working"`` (default), ``"episodic"``, or
            ``"long_term"``.

    Returns:
        A dict with:
            - ``found`` — bool
            - ``key``, ``tier`` — echo of input
            - ``content`` — entry text (truncated to
              ``_MAX_CONTENT_CHARS`` so a huge entry
              doesn't blow the MCP response).
            - ``content_truncated`` — bool
            - ``source`` — provenance label
              (``"working" | "chroma" | "letta" | "fallback"``)
            - ``created_at``, ``created_at_ts``
            - ``freshness`` — ``"FRESH" | "RECENT" | "OLD"``
              per ``supervisor.mechanisms.freshness``
            - ``metadata`` — full metadata dict (session_id,
              dag_status, etc.)
            - ``errors`` — list of error strings.
    """
    tier_lc = (tier or "working").lower()
    out: dict[str, Any] = {
        "found":             False,
        "key":               key,
        "tier":              tier_lc,
        "content":           "",
        "content_truncated": False,
        "source":            "",
        "created_at":        "",
        "created_at_ts":     0.0,
        "freshness":         "",
        "metadata":          {},
        "errors":            [],
    }
    if not key:
        out["errors"].append("key is required")
        return out
    if tier_lc not in _VALID_TIERS:
        out["errors"].append(f"unknown tier {tier!r}; expected one of {_VALID_TIERS}")
        return out

    try:
        registry = get_registry()
        mm = registry.memory_manager
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(f"registry: {exc}")
        return out

    try:
        layer = getattr(mm, tier_lc, None)
        if layer is None:
            out["errors"].append(f"manager has no {tier_lc!r} layer")
            return out
        entry = await _get_entry(mm, tier_lc, _DEFAULT_ROLE, key)
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(f"{tier_lc}.get: {exc}")
        return out

    if entry is None:
        out["errors"].append(f"key {key!r} not found in tier {tier_lc!r}")
        return out

    content = getattr(entry, "content", "") or ""
    if len(content) > _MAX_CONTENT_CHARS:
        content = content[:_MAX_CONTENT_CHARS] + "\n[…truncated…]"
        out["content_truncated"] = True
    meta = getattr(entry, "metadata", {}) or {}
    try:
        created_at_ts = float(meta.get("created_at_ts") or 0)
    except (TypeError, ValueError):
        created_at_ts = 0.0

    # Freshness label via the same function GOAT uses. When
    # the timestamp is 0 (legacy entries with no metadata)
    # the label falls back to "OLD" via the loader.
    try:
        from supervisor.mechanisms.freshness import score_freshness
        freshness = score_freshness({"created_at_ts": created_at_ts},
                                    datetime.now(tz=timezone.utc).timestamp())
        out["freshness"] = freshness
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(f"score_freshness: {exc}")

    out["content"] = content
    out["source"] = getattr(entry, "source", "") or ""
    out["created_at"] = getattr(entry, "created_at", "") or ""
    out["created_at_ts"] = created_at_ts
    out["metadata"] = dict(meta) if isinstance(meta, dict) else {}
    out["found"] = True
    return out


# ════════════════════════════════════════════════════════════════════════════
# 5. list_dangling_dag_entries
# ════════════════════════════════════════════════════════════════════════════


# Cap to keep the MCP response small. The dangling-DAG list
# is mostly used to audit cleanup, not for runtime lookup.
_MAX_DANGLING: Final[int] = 200


async def list_dangling_dag_entries(
    tier: str = "episodic",
    limit: int = 100,
) -> dict[str, Any]:
    """Return episodic (or working) entries that look stale / dangling.

    "Dangling" means one of:
      1. ``metadata.stale is True`` — explicitly marked.
      2. ``metadata.dag_status in {"completed", "done", "failed",
         "cancelled"}`` — the DAG finished and this note was
         never cleaned up.
      3. Key namespace is ``DAG`` AND age >
         ``dag_max_age_seconds`` (from
         ``config/memory.toml [freshness]``) — the freshness
         rule ``is_stale`` would have excluded this entry
         from the working-memory block on the next turn.

    Args:
        tier: Where to scan. ``"episodic"`` (default) holds
            promoted turns and DAG notes; ``"working"`` holds
            the current session's records.
        limit: Max entries to return (default 100, hard-capped
            at ``_MAX_DANGLING``).

    Returns:
        A dict with:
            - ``found`` — bool
            - ``entries`` — list of dicts (oldest first), each
              with ``key``, ``content_preview``, ``created_at``,
              ``created_at_ts``, ``age_s``, ``staleness_reason``,
              ``dag_status`` (when present in metadata),
              ``source``, ``freshness``.
            - ``tier``, ``limit``, ``dag_max_age_seconds`` —
              echo of input + the threshold used.
            - ``total_scanned`` — total episodic entries
              inspected (before filtering).
            - ``errors`` — per-step error strings.
    """
    tier_lc = (tier or "episodic").lower()
    out: dict[str, Any] = {
        "found":              False,
        "entries":            [],
        "tier":               tier_lc,
        "limit":              int(limit),
        "dag_max_age_seconds": 0.0,
        "total_scanned":      0,
        "errors":             [],
    }
    if tier_lc not in _VALID_TIERS:
        out["errors"].append(f"unknown tier {tier!r}; expected one of {_VALID_TIERS}")
        return out
    cap = max(1, min(int(limit or 100), _MAX_DANGLING))
    out["limit"] = cap

    try:
        registry = get_registry()
        mm = registry.memory_manager
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(f"registry: {exc}")
        return out

    # Read the configured threshold so the operator can see
    # what "too old" actually means.
    try:
        from supervisor.mechanisms.freshness import load_freshness_config
        freshness_cfg = load_freshness_config()
        out["dag_max_age_seconds"] = float(
            freshness_cfg.get("dag_max_age_seconds", 600.0),
        )
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(f"load_freshness_config: {exc}")
        out["dag_max_age_seconds"] = 600.0  # safe default
    dag_max_age = out["dag_max_age_seconds"]

    # Overfetch because the tier also contains non-DAG
    # entries (e.g. user_session turns). We scan up to
    # 4×cap so a small dangling ratio still surfaces.
    scan_limit = max(cap * 4, cap)
    try:
        layer = getattr(mm, tier_lc, None)
        if layer is None:
            out["errors"].append(f"manager has no {tier_lc!r} layer")
            return out
        records = await layer.list(_DEFAULT_ROLE, limit=scan_limit)
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(f"{tier_lc}.list: {exc}")
        return out

    out["total_scanned"] = len(records or [])
    now = datetime.now(tz=timezone.utc).timestamp()
    dag_status_done = {"completed", "done", "failed", "cancelled", "aborted"}
    dangling: list[dict[str, Any]] = []

    for rec in records or []:
        key = getattr(rec, "key", "") or ""
        if not key:
            continue
        meta = getattr(rec, "metadata", {}) or {}
        try:
            ts = float(meta.get("created_at_ts") or 0)
        except (TypeError, ValueError):
            ts = 0.0

        # Determine staleness reason. Order matters — the
        # first matching reason wins so the operator gets
        # the most specific explanation.
        reason: str | None = None
        if meta.get("stale") is True:
            reason = "metadata.stale=True"
        dag_status = str(meta.get("dag_status") or "").lower()
        if not reason and dag_status in dag_status_done:
            reason = f"metadata.dag_status={dag_status}"

        # Namespace-based staleness check. Uses the same
        # ``classify_namespace`` rule GOAT's staleness
        # mechanism uses, so we agree on what "DAG" means.
        if not reason:
            try:
                from supervisor.mechanisms.namespace import classify_namespace
                if classify_namespace(key) == "DAG":
                    age = (now - ts) if ts else None
                    if age is None or age > dag_max_age:
                        reason = (
                            f"namespace=DAG, age>{dag_max_age}s"
                            if age is not None
                            else "namespace=DAG, no timestamp"
                        )
            except Exception as exc:  # noqa: BLE001
                out["errors"].append(f"classify_namespace: {exc}")

        if not reason:
            continue

        content = getattr(rec, "content", "") or ""
        preview = content[:200] + ("…" if len(content) > 200 else "")
        try:
            from supervisor.mechanisms.freshness import score_freshness
            freshness = score_freshness(
                {"created_at_ts": ts}, now,
            )
        except Exception:
            freshness = "OLD"
        dangling.append({
            "key":              key,
            "content_preview":  preview,
            "created_at":       getattr(rec, "created_at", "") or "",
            "created_at_ts":    ts,
            "age_s":            (now - ts) if ts else None,
            "staleness_reason": reason,
            "dag_status":       dag_status or None,
            "source":           getattr(rec, "source", "") or "",
            "freshness":        freshness,
        })

    # Oldest first — the operator is auditing cleanup,
    # so the oldest stale entries are the most pressing.
    dangling.sort(key=lambda e: e.get("created_at_ts") or 0)
    if len(dangling) > cap:
        dangling = dangling[:cap]
    out["entries"] = dangling
    out["found"] = bool(dangling)
    return out


# ════════════════════════════════════════════════════════════════════════════
# 6. get_current_system_prompt
# ════════════════════════════════════════════════════════════════════════════


async def get_current_system_prompt(
    include_style: bool = True,
) -> dict[str, Any]:
    """Return the literal system prompt GOAT sends to the LLM RIGHT NOW.

    Calls the same ``build_system_prompt`` function the supervisor
    uses, with the style profile pulled from Letta (or an empty
    string when Letta is unreachable). Useful for prompt-
    engineering debugging — confirm which rules are active without
    mentally re-running the prompt builder.

    Args:
        include_style: When True (default), the rendered prompt
            includes the style directive loaded from Letta.
            When False, the prompt is the bare ``GOAT_SYSTEM``
            rules block — useful for isolating the operational
            rules from the personalization layer.

    Returns:
        A dict with:
            - ``prompt`` — the composed system prompt string.
            - ``prompt_chars`` / ``prompt_tokens_est`` —
              size hints (4-chars-per-token heuristic).
            - ``style_chars`` — size of the style directive
              included (``0`` when ``include_style=False`` or
              when Letta returned no profile).
            - ``style_included`` — bool
            - ``errors`` — list of error strings.
    """
    out: dict[str, Any] = {
        "prompt":          "",
        "prompt_chars":    0,
        "prompt_tokens_est": 0,
        "style_chars":     0,
        "style_included":  False,
        "errors":          [],
    }
    style: str = ""
    if include_style:
        try:
            registry = get_registry()
            from supervisor.behavior.store import load_style
            style = (await load_style(registry.memory_manager)) or ""
        except Exception as exc:  # noqa: BLE001
            out["errors"].append(f"load_style: {exc}")

    try:
        from supervisor.pipeline.prompt_helpers import build_system_prompt
        prompt = build_system_prompt(style)
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(f"build_system_prompt: {exc}")
        # Fall back to the raw GOAT_SYSTEM constant so the
        # operator at least sees the rules.
        try:
            from supervisor.identity import GOAT_SYSTEM
            prompt = GOAT_SYSTEM
        except Exception as inner:  # noqa: BLE001
            out["errors"].append(f"identity.GOAT_SYSTEM: {inner}")
            return out

    out["prompt"] = prompt
    out["prompt_chars"] = len(prompt)
    out["prompt_tokens_est"] = len(prompt) // 4
    out["style_chars"] = len(style or "")
    out["style_included"] = bool(include_style and style)
    return out


# ════════════════════════════════════════════════════════════════════════════
# 7. get_recent_tool_calls
# ════════════════════════════════════════════════════════════════════════════


# The supervisor writes one ``turn:<n>:actions`` record per turn
# that had at least one tool call. The payload is a JSON list of
# ``{tool, ok, summary, args}`` dicts (see
# ``supervisor.session.turn_persistence._action_log_from_turn``).
_ACTION_LOG_KEY: Final[str] = "turn:{n}:actions"
_MAX_TOOL_CALLS: Final[int] = 500


async def get_recent_tool_calls(
    session_id: str = "",
    last_n_turns: int = 5,
) -> dict[str, Any]:
    """Flat chronological list of every tool call in recent turns.

    The fast path to answer "did the model call memory_delete or
    did it just talk about it?" — pulls the structured action
    records the supervisor writes via ``store_action_log``.

    Args:
        session_id: Optional session filter. Empty string
            (default) returns calls across ALL sessions —
            useful when the operator doesn't know the
            session id. Pass an explicit UUID to scope to
            one session.
        last_n_turns: How many of the most recent matching
            turns to inspect (default 5, hard-capped at
            ``_MAX_TRACE_TURNS``).

    Returns:
        A dict with:
            - ``found`` — bool
            - ``tool_calls`` — list of dicts (most recent
              last), each with ``turn``, ``tool``, ``ok``,
              ``summary``, ``session_id``.
            - ``turn_count`` — how many turns were scanned.
            - ``by_tool`` — ``{tool_name: count}`` summary,
              handy for "model called memory_search 8
              times, that's the loop".
            - ``last_n_turns``, ``session_id`` — echo.
            - ``errors`` — list of error strings.
    """
    cap = max(1, min(int(last_n_turns or 5), _MAX_TRACE_TURNS))
    out: dict[str, Any] = {
        "found":        False,
        "tool_calls":   [],
        "turn_count":   0,
        "by_tool":      {},
        "session_id":   session_id,
        "last_n_turns": cap,
        "errors":       [],
    }

    # First: list turn:* records to learn which turn numbers
    # + session_ids to inspect. Reuses the same traversal
    # get_session_trace uses so the operator can chain
    # ``get_recent_tool_calls(session_id)`` after a
    # ``get_session_trace()`` without re-discovering the
    # session_id by hand.
    try:
        registry = get_registry()
        mm = registry.memory_manager
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(f"registry: {exc}")
        return out

    # Inline minimal copy of get_session_trace's discovery
    # logic so we can also know which turns belong to which
    # session, without rewriting it as a helper.
    raw = await _list_turn_records(mm, "working", _DEFAULT_ROLE, cap * 4)
    if not raw:
        out["errors"].append("no turn:* records in working memory")
        return out

    # Build (turn_number, session_id, key) for the matching
    # turns, then sort by turn number (most recent last) and
    # take the last ``cap`` of them.
    candidates: list[tuple[int, str, str]] = []
    for rec in raw:
        meta = getattr(rec, "metadata", {}) or {}
        rec_session = str(meta.get("session_id") or "")
        key = getattr(rec, "key", "") or ""
        # Action-log keys are ``turn:<n>:actions``. Turn
        # summary keys are ``turn:<n>:intent`` /
        # ``turn:<n>:summary``. We use the intent/summary
        # keys (one per turn) as the discovery anchor so we
        # always see one entry per turn, even when the turn
        # had no actions (then the actions record is missing
        # and the turn just contributes 0 calls).
        if not key.startswith("turn:"):
            continue
        # Filter to session_id (when supplied).
        if session_id and rec_session != session_id:
            continue
        try:
            turn_n = int(key.split(":")[1])
        except (IndexError, ValueError):
            continue
        candidates.append((turn_n, rec_session, key))
    if not candidates:
        out["errors"].append(f"no turns found for session_id={session_id!r}")
        return out
    candidates.sort(key=lambda t: t[0])
    selected = candidates[-cap:]

    # Now fetch the action-log record for each selected turn.
    tool_calls: list[dict[str, Any]] = []
    by_tool: dict[str, int] = {}
    turns_scanned = 0
    for turn_n, rec_session, _key in selected:
        turns_scanned += 1
        action_key = _ACTION_LOG_KEY.format(n=turn_n)
        try:
            entry = await _get_entry(mm, "working", _DEFAULT_ROLE, action_key)
        except Exception as exc:  # noqa: BLE001
            out["errors"].append(f"working.get({action_key}): {exc}")
            continue
        if entry is None:
            # Turn had no tool calls — the supervisor
            # silently skipped writing an actions record.
            # We just move on; this is normal.
            continue
        raw_payload = (getattr(entry, "content", "") or "").strip()
        try:
            entries = json.loads(raw_payload)
        except (TypeError, ValueError) as exc:
            out["errors"].append(
                f"{action_key}: bad JSON ({exc}); raw_len={len(raw_payload)}",
            )
            continue
        if not isinstance(entries, list):
            out["errors"].append(
                f"{action_key}: payload is {type(entries).__name__}, expected list",
            )
            continue
        for e in entries:
            if not isinstance(e, dict):
                continue
            tool = str(e.get("tool") or "?")
            summary = str(e.get("summary") or "")
            ok = bool(e.get("ok"))
            tool_calls.append({
                "turn":       turn_n,
                "tool":       tool,
                "ok":         ok,
                "summary":    summary,
                "session_id": rec_session,
            })
            by_tool[tool] = by_tool.get(tool, 0) + 1

    if len(tool_calls) > _MAX_TOOL_CALLS:
        tool_calls = tool_calls[-_MAX_TOOL_CALLS:]
    out["tool_calls"] = tool_calls
    out["turn_count"] = turns_scanned
    out["by_tool"] = by_tool
    out["found"] = bool(tool_calls)
    return out


# ════════════════════════════════════════════════════════════════════════════
# MCP wiring
# ════════════════════════════════════════════════════════════════════════════


def register(server) -> None:
    """Register the four state-query tools on an MCP ``Server``.

    Args:
        server: An ``mcp.server.Server`` instance (the
            FastMCP wrapper). This function attaches the
            four new tools and is idempotent.
    """
    @server.tool(
        name="search_logs",
        description=(
            "Grep-style search across the recent log window. Pass a regex (Python re.search "
            "semantics) or a plain substring. Filter by level (ALL/DEBUG/INFO/WARNING/ERROR/CRITICAL) "
            "and a time window in minutes. Returns matching lines + match_count + truncated flag. "
            "READ-ONLY — safe to run while the Telegram bot is active."
        ),
    )
    async def _search_logs(
        pattern: str = "",
        level: str = "ALL",
        minutes: int = 60,
        limit: int = 100,
    ) -> dict[str, Any]:
        return search_logs(pattern=pattern, level=level, minutes=minutes, limit=limit)

    @server.tool(
        name="get_session_trace",
        description=(
            "Return the last N turns of a session as a structured trace. Each turn includes "
            "intent, summary, action (direct/clarify/dag), called_tools, source, and timestamps. "
            "Reads from working memory by default (turn:* records the supervisor wrote). "
            "Pass session_id='' to list ALL recent turns across sessions."
        ),
    )
    async def _get_session_trace(
        session_id: str = "",
        last_n_turns: int = 10,
        tier: str = "working",
    ) -> dict[str, Any]:
        return await get_session_trace(
            session_id=session_id, last_n_turns=last_n_turns, tier=tier,
        )

    @server.tool(
        name="get_supervisor_state",
        description=(
            "Return the live-ish state of a supervisor: history_length, last_turn, last_action, "
            "last_called_tools. When session_id is None, picks the most "
            "recent supervisor that wrote a turn. The MCP server reconstructs a snapshot from the "
            "turn:* records the bot's GoatSupervisor wrote to working memory."
        ),
    )
    async def _get_supervisor_state(session_id: str | None = None) -> dict[str, Any]:
        return await get_supervisor_state(session_id=session_id)

    @server.tool(
        name="get_memory_entry",
        description=(
            "Fetch a single memory entry by exact key. Returns full content (truncated to 8KB), "
            "metadata, source, created_at, freshness label. Tier: working | episodic | long_term. "
            "READ-ONLY."
        ),
    )
    async def _get_memory_entry(key: str, tier: str = "working") -> dict[str, Any]:
        return await get_memory_entry(key=key, tier=tier)

    @server.tool(
        name="list_dangling_dag_entries",
        description=(
            "Return episodic (or working) entries that look stale / dangling: marked "
            "metadata.stale=True, metadata.dag_status in {completed, done, failed, cancelled, aborted}, "
            "or in the DAG namespace with age > dag_max_age_seconds (from config/memory.toml [freshness]). "
            "Sorted oldest-first so cleanup is easy. Returns staleness_reason per entry. READ-ONLY."
        ),
    )
    async def _list_dangling_dag_entries(
        tier: str = "episodic",
        limit: int = 100,
    ) -> dict[str, Any]:
        return await list_dangling_dag_entries(tier=tier, limit=limit)

    @server.tool(
        name="get_current_system_prompt",
        description=(
            "Return the literal system prompt GOAT sends to the LLM right now — same call as "
            "pipeline.prompt_helpers.build_system_prompt makes (GOAT_SYSTEM + optional style directive "
            "from Letta). Returns prompt + char/token counts. include_style=False returns bare rules. "
            "Useful for prompt-engineering debugging. READ-ONLY."
        ),
    )
    async def _get_current_system_prompt(
        include_style: bool = True,
    ) -> dict[str, Any]:
        return await get_current_system_prompt(include_style=include_style)

    @server.tool(
        name="get_recent_tool_calls",
        description=(
            "Flat chronological list of every tool call in the last N turns of a session. Each row has "
            "turn, tool, ok, summary (one-line result preview). Sourced from the structured "
            "turn:<n>:actions records the supervisor writes. Pass session_id='' to span all sessions. "
            "Fast path to answer 'did the model call memory_delete or just talk about it?' — no grep."
        ),
    )
    async def _get_recent_tool_calls(
        session_id: str = "",
        last_n_turns: int = 5,
    ) -> dict[str, Any]:
        return await get_recent_tool_calls(
            session_id=session_id, last_n_turns=last_n_turns,
        )
