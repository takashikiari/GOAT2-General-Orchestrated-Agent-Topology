"""utils.logging.tail — shared log-tail logic for the get_recent_logs tool and
the admin panel's /api/logs route. One implementation of "read a log file,
filter by cutoff/level, cap line count" so the two never drift.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

LEVELS = frozenset({"ALL", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})

__all__ = ["LEVELS", "tail_log"]


def _level_matches(line: str, level: str) -> bool:
    """True when ``line`` carries level token ``level`` (ALL = everything)."""
    if not level or level.upper() == "ALL":
        return True
    return f" {level.upper()} " in line.upper()


def _parse_ts(line: str) -> datetime | None:
    """Parse the leading ``YYYY-MM-DDTHH:MM:SS`` timestamp, or None."""
    try:
        return datetime.strptime(line[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def tail_log(path: Path, minutes: int, level: str, limit: int, max_lines: int) -> list[str]:
    """Return matching log lines from the last ``minutes`` minutes, oldest-first.

    Raises:
        ValueError: ``level`` is not a recognized level.
        FileNotFoundError: ``path`` does not exist.
        OSError: ``path`` exists but could not be read.
    """
    lvl = (level or "ALL").upper()
    if lvl not in LEVELS:
        raise ValueError(f"unknown level {level!r}; expected one of {sorted(LEVELS - {'ALL'})} or ALL")
    if not path.exists():
        raise FileNotFoundError(str(path))
    cutoff = datetime.now() - timedelta(minutes=max(0, int(minutes)))
    cap = max(1, min(int(limit), max_lines))
    matched: list[str] = []
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            ts = _parse_ts(line)
            if ts is not None and ts < cutoff:
                continue
            if not _level_matches(line, lvl):
                continue
            matched.append(line.rstrip("\n"))
    if len(matched) > cap:
        matched = matched[-cap:]
    return matched
