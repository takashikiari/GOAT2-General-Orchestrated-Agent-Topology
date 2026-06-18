from __future__ import annotations

import logging
import os
from tools._make_tool import make_tool

log = logging.getLogger("goat2.tools.system")

# Derive log file path from GOAT_WORKSPACE env var (falls back to repo logs/ dir).
_WORKSPACE = os.environ.get("GOAT_WORKSPACE")
if _WORKSPACE:
    LOG_FILE = os.path.join(_WORKSPACE, "logs", "goat2.log")
else:
    # Fallback: relative to this file's parent (works from any CWD)
    _here = os.path.dirname(os.path.abspath(__file__))
    LOG_FILE = os.path.normpath(
        os.path.join(_here, "..", "..", "logs", "goat2.log")
    )

async def _read_logs_handler(level: str = "ERROR", lines: int = 50) -> str:
    """Read last N lines from log file filtered by level."""
    try:
        with open(LOG_FILE) as f:
            all_lines = f.readlines()
        if level.upper() != "ALL":
            filtered = [l for l in all_lines if level.upper() in l]
        else:
            filtered = all_lines
        result = "".join(filtered[-lines:])
        return result if result.strip() else f"No {level} entries found."
    except FileNotFoundError:
        return "Log file not found — bot not yet started with file logging."
    except Exception as e:
        return f"ERROR reading logs: {e}"

READ_LOGS = make_tool(
    name="read_logs",
    description="Read recent log entries filtered by level (ERROR, WARNING, INFO, ALL). Use for system diagnostics.",
    parameters={
        "type": "object",
        "properties": {
            "level": {"type": "string", "enum": ["ERROR", "WARNING", "INFO", "ALL"], "default": "ERROR"},
            "lines": {"type": "integer", "default": 50, "description": "Number of lines to return"},
        },
    },
    handler=_read_logs_handler,
)
