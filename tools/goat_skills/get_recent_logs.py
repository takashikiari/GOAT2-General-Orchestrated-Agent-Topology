"""goat_skills.get_recent_logs — on-demand live log-tail tool.

GOAT calls this when asked about its own recent logs, warnings, or errors.
Reads the exact file ``utils.logging.setup`` writes (via the shared ``LOG_FILE``
constant), returns lines from the last ``minutes`` minutes, optionally filtered
by level. On-demand only — no always-on context injection.

Tailing logic lives in ``utils.logging.tail.tail_log`` — shared with the admin
panel's ``/api/logs`` route so the two never drift.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from orchestrator.tools import ToolDefinition
from tools.get_recent_logs_config import GET_RECENT_LOGS_MAX_LINES as _MAX_LINES
from utils.logging.setup import LOG_FILE
from utils.logging.tail import tail_log

if TYPE_CHECKING:
    from registry.registry import ServiceRegistry

__all__ = ["build"]

_DESCRIPTION = (
    "Recent lines from GOAT's own log file, optionally filtered by level. "
    "Use this when the user asks to see recent logs, warnings, or errors, or "
    "what happened recently. Returns the last N minutes (default 30)."
)


def build(registry: "ServiceRegistry") -> list[ToolDefinition]:
    """Build the get_recent_logs tool, reading the shared log file."""
    async def handler(minutes: int = 30, level: str = "ALL", limit: int = 100, chat_id: str = "") -> str:
        """Return matching log lines from the last ``minutes`` minutes."""
        try:
            matched = tail_log(LOG_FILE, minutes, level, limit, _MAX_LINES)
        except ValueError as exc:
            return f"({exc})"
        except FileNotFoundError:
            return f"(log file not found: {LOG_FILE})"
        except OSError as exc:
            return f"(error reading log file: {exc})"
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
