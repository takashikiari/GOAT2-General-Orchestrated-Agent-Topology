"""goat_skills.get_recent_logs — on-demand live log-tail tool.

GOAT calls this when asked about its own recent logs, warnings, or errors.
Reads the exact file ``utils.logging.setup`` writes (via the shared ``LOG_FILE``
constant), returns lines from the last ``minutes`` minutes, optionally filtered
by level. On-demand only — no always-on context injection.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from orchestrator.tools import ToolDefinition
from tools.get_recent_logs_config import GET_RECENT_LOGS_MAX_LINES as _MAX_LINES
from utils.logging.setup import LOG_FILE

if TYPE_CHECKING:
    from registry.registry import ServiceRegistry

__all__ = ["build"]

_LEVELS = frozenset({"ALL", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
_DESCRIPTION = (
    "Recent lines from GOAT's own log file, optionally filtered by level. "
    "Use this when the user asks to see recent logs, warnings, or errors, or "
    "what happened recently. Returns the last N minutes (default 30)."
)


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


def build(registry: "ServiceRegistry") -> list[ToolDefinition]:
    """Build the get_recent_logs tool, reading the shared log file."""
    async def handler(minutes: int = 30, level: str = "ALL", limit: int = 100, chat_id: str = "") -> str:
        """Return matching log lines from the last ``minutes`` minutes."""
        lvl = (level or "ALL").upper()
        if lvl not in _LEVELS:
            return f"(unknown level {level!r}; expected one of {sorted(_LEVELS - {'ALL'})} or ALL)"
        path = LOG_FILE
        if not path.exists():
            return f"(log file not found: {path})"
        cutoff = datetime.now() - timedelta(minutes=max(0, int(minutes)))
        cap = max(1, min(int(limit), _MAX_LINES))
        matched: list[str] = []
        try:
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    ts = _parse_ts(line)
                    if ts is not None and ts < cutoff:
                        continue
                    if not _level_matches(line, lvl):
                        continue
                    matched.append(line.rstrip("\n"))
        except OSError as exc:
            return f"(error reading log file: {exc})"
        if len(matched) > cap:
            matched = matched[-cap:]
        if not matched:
            return f"(no matching log lines in the last {minutes} minute(s))"
        return "\n".join(matched)

    return [ToolDefinition(
        name="get_recent_logs",
        description=_DESCRIPTION,
        parameters={
            "type": "object",
            "properties": {
                "minutes": {"type": "integer", "description": "Look-back window in minutes (default 30)"},
                "level": {"type": "string", "description": "Filter: ALL/DEBUG/INFO/WARNING/ERROR/CRITICAL (default ALL)"},
                "limit": {"type": "integer", "description": f"Max lines to return (default 100, max {_MAX_LINES})"},
            },
        },
        handler=handler,
    )]