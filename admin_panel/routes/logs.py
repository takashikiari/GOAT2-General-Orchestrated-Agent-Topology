"""admin_panel.routes.logs — read-only /api/logs over the shared tail_log helper."""
from __future__ import annotations

from fastapi import APIRouter

from tools.get_recent_logs_config import GET_RECENT_LOGS_MAX_LINES as _MAX_LINES
from utils.logging.setup import LOG_FILE, get_logger
from utils.logging.tail import tail_log

router = APIRouter()
log = get_logger(__name__)


@router.get("/api/logs")
async def get_logs(minutes: int = 30, level: str = "ALL", limit: int = 100) -> dict:
    try:
        lines = tail_log(LOG_FILE, minutes, level, limit, _MAX_LINES)
    except ValueError as exc:
        # Our own input-validation message (invalid `level`) — safe to
        # return as-is, unlike the exceptions below which wrap arbitrary
        # OS/filesystem error text.
        return {"error": str(exc)}
    except FileNotFoundError:
        log.warning("get_logs: log file not found: %s", LOG_FILE)
        return {"error": "log file not found"}
    except OSError as exc:
        log.warning("get_logs: error reading log file: %s", exc)
        return {"error": "error reading log file"}
    return {"lines": lines}
