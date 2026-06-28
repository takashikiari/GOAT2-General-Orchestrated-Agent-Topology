"""
utils.logging.setup — structured logging configuration for GOAT 2.0.

Usage in every module:
    from utils.logging.setup import get_logger
    log = get_logger(__name__)

get_logger() configures the Python root logger on first call (stdout at INFO +
rotating file at DEBUG).  Noisy third-party libraries (httpx, openai, httpcore)
are capped at WARNING so they don't drown out application logs.  All subsequent
calls return a named logger cheaply — no re-configuration.

For symbol-conflict detection see utils.logging.symbols.register_symbols().
"""
from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path

_LOG_DIR = Path(os.environ.get("GOAT_LOG_DIR", "/tmp/goat2/logs"))
_LOG_FILE = _LOG_DIR / "goat2.log"

# Public alias so other modules (e.g. the get_recent_logs plugin) read the
# exact file this module writes — one source of truth for the log path.
LOG_FILE: Path = _LOG_FILE
_FMT = "%(asctime)s  %(name)-35s  %(levelname)-8s  %(message)s"
_DATE_FMT = "%Y-%m-%dT%H:%M:%S"
_QUIET_LIBS = ("httpx", "httpcore", "openai", "hpack", "h2", "asyncio")
_root_configured = False


def get_logger(name: str) -> logging.Logger:
    """
    Return a logger for ``name``, configuring the root logger on first call.

    Writes INFO+ to stdout and DEBUG+ to a rotating log file (10 MB, 5 backups).
    Third-party libraries that log excessively are capped at WARNING.

    Args:
        name: Typically ``__name__`` of the calling module.

    Returns:
        Configured logging.Logger instance.
    """
    global _root_configured
    if not _root_configured:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        root = logging.getLogger()
        root.setLevel(logging.DEBUG)

        fmt = logging.Formatter(_FMT, datefmt=_DATE_FMT)

        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        sh.setLevel(logging.INFO)
        root.addHandler(sh)

        fh = logging.handlers.RotatingFileHandler(
            _LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5
        )
        fh.setFormatter(fmt)
        fh.setLevel(logging.DEBUG)
        root.addHandler(fh)

        for lib in _QUIET_LIBS:
            logging.getLogger(lib).setLevel(logging.WARNING)

        _root_configured = True

    return logging.getLogger(name)
