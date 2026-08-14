"""admin_panel.routes.logs — read-only /api/logs over the shared tail_log helper."""
from __future__ import annotations

from fastapi import APIRouter

from tools.get_recent_logs_config import GET_RECENT_LOGS_MAX_LINES as _MAX_LINES
from utils.logging.setup import LOG_FILE
from utils.logging.tail import tail_log

router = APIRouter()


@router.get("/api/logs")
async def get_logs(minutes: int = 30, level: str = "ALL", limit: int = 100) -> dict:
    try:
        lines = tail_log(LOG_FILE, minutes, level, limit, _MAX_LINES)
    except ValueError as exc:
        return {"error": str(exc)}
    except FileNotFoundError:
        return {"error": f"log file not found: {LOG_FILE}"}
    except OSError as exc:
        return {"error": f"error reading log file: {exc}"}
    return {"lines": lines}
