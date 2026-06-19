"""Log-file query tools — read ``~/workspace/goat2/logs/goat2.log``
and return lines from the last N minutes, optionally filtered
by log level.

READ-ONLY: the file is opened with ``open(..., "r")`` only.
No writes, no locks. Safe to run concurrently with
``telegram_bot.py`` (the bot also opens the same file read-only
when tailing is enabled, and writes via Python's ``logging``
module under a ``FileHandler`` lock that does not block
readers).

USAGE (from the MCP server):
    from mcp_server.tools.query_logs import register
    register(server)

USAGE (programmatic):
    from mcp_server.tools.query_logs import (
        get_recent_logs, get_errors,
    )
    text = get_recent_logs(minutes=30, level="ALL")
    errors = get_errors(minutes=1440)
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("goat2.mcp_server.tools.query_logs")

__all__ = ["get_recent_logs", "get_errors", "register"]


# Path resolution: the log lives at <repo_root>/logs/goat2.log.
# We compute ``repo_root`` as ``mcp_server/__init__.py``'s
# parent's parent (one level for the package, one for the
# repo root). The path is resolved once at import time so
# repeated calls don't re-traverse the filesystem.
_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_LOG_PATH: Path = _REPO_ROOT / "logs" / "goat2.log"

# Max lines returned by either tool. Bounds prompt growth;
# the log file can be tens of thousands of lines after a
# long Telegram session.
_MAX_LINES: int = 1_000

# Recognized level tokens for the ``level`` filter on
# ``get_recent_logs``. ``"ALL"`` (case-insensitive) is a
# wildcard that matches every line.
_LEVEL_TOKENS: frozenset[str] = frozenset({
    "ALL", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL",
})


def _parse_log_timestamp(line: str) -> datetime | None:
    """Parse the ``YYYY-MM-DD HH:MM:SS,fff`` prefix of a log line.

    Args:
        line: A raw log line.

    Returns:
        The parsed UTC ``datetime``, or ``None`` when the line
        does not begin with a parseable timestamp.
    """
    # The format is fixed: 23 chars of timestamp + space.
    if len(line) < 24:
        return None
    head = line[:23]
    try:
        return datetime.strptime(head, "%Y-%m-%d %H:%M:%S,%f").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _level_matches(line: str, level: str) -> bool:
    """True when ``line``'s level token matches ``level``.

    The ``logging`` formatter places the level token at a
    fixed column (after the timestamp + logger name). For
    robustness we do a case-insensitive substring match.
    """
    if not level or level.upper() == "ALL":
        return True
    needle = " " + level.upper() + " "
    return needle in line.upper()


def _read_window(minutes: int, predicate) -> str:
    """Read the log file and return matching lines as a single string.

    Args:
        minutes: Only lines whose timestamp is within the last
            ``minutes`` minutes (relative to now) are considered.
        predicate: Callable ``line -> bool`` applied after the
            time-window filter.

    Returns:
        Concatenated matching lines. Empty string when the
        log file is missing or no lines match.
    """
    if not _LOG_PATH.exists():
        return f"(log file not found: {_LOG_PATH})"
    cutoff = datetime.now(tz=timezone.utc) - timedelta(minutes=max(0, int(minutes)))
    matched: list[str] = []
    try:
        # ``errors="replace"`` so a single bad UTF-8 byte
        # doesn't blow up the read.
        with _LOG_PATH.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                ts = _parse_log_timestamp(line)
                if ts is not None and ts < cutoff:
                    continue
                if not predicate(line):
                    continue
                matched.append(line.rstrip("\n"))
    except OSError as exc:
        return f"(error reading log file: {exc})"
    # Cap output to avoid blowing the MCP response size limit.
    if len(matched) > _MAX_LINES:
        omitted = len(matched) - _MAX_LINES
        matched = matched[-_MAX_LINES:]
        matched.insert(0, f"(... {omitted} earlier line(s) omitted ...)")
    if not matched:
        return f"(no matching log lines in the last {minutes} minute(s))"
    return "\n".join(matched)


def get_recent_logs(minutes: int = 30, level: str = "ALL") -> str:
    """Return log lines from the last ``minutes`` minutes.

    Args:
        minutes: Time window. Default 30. Must be non-negative;
            values <= 0 are clamped to 0 (returns only the
            very last line if any).
        level: One of ``"ALL"``, ``"DEBUG"``, ``"INFO"``,
            ``"WARNING"``, ``"ERROR"``, ``"CRITICAL"``
            (case-insensitive). ``"ALL"`` matches everything.

    Returns:
        Newline-joined matching log lines (most recent last),
        capped at ``_MAX_LINES`` lines. Empty result wrapped
        in a human-readable message.
    """
    lvl = (level or "ALL").upper()
    if lvl not in _LEVEL_TOKENS:
        return f"(unknown level {level!r}; expected one of {sorted(_LEVEL_TOKENS - {'ALL'})} or ALL)"
    return _read_window(minutes, lambda line: _level_matches(line, lvl))


def get_errors(minutes: int = 1440) -> str:
    """Return ERROR + WARNING + CRITICAL lines from the last ``minutes``.

    Args:
        minutes: Time window. Default 1440 (24 h).

    Returns:
        Newline-joined matching log lines, capped at
        ``_MAX_LINES`` lines.
    """
    def is_problem(line: str) -> bool:
        return _level_matches(line, "ERROR") or \
               _level_matches(line, "WARNING") or \
               _level_matches(line, "CRITICAL")
    return _read_window(minutes, is_problem)


# ── MCP wiring ────────────────────────────────────────────────

def register(server) -> None:
    """Register the two log tools on an ``mcp.server.Server``.

    Args:
        server: An ``mcp.server.Server`` instance (the SDK
            supports registering tool handlers via decorators
            on the server). This function attaches two tools
            and is idempotent — calling it twice on the same
            server is safe.
    """
    @server.tool(
        name="get_recent_logs",
        description=(
            "Read GOAT's log file and return matching lines from the last N minutes. "
            "Filter by level: ALL / DEBUG / INFO / WARNING / ERROR / CRITICAL. "
            "READ-ONLY — safe to run while the Telegram bot is active."
        ),
    )
    async def _get_recent_logs(minutes: int = 30, level: str = "ALL") -> str:
        return get_recent_logs(minutes=minutes, level=level)

    @server.tool(
        name="get_errors",
        description=(
            "Return only ERROR / WARNING / CRITICAL log lines from the last N minutes "
            "(default 1440 = 24 h). Useful for 'why is GOAT broken right now?' triage."
        ),
    )
    async def _get_errors(minutes: int = 1440) -> str:
        return get_errors(minutes=minutes)