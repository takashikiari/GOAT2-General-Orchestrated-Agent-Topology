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



def default_log_dir(environ: "os._Environ[str] | dict[str, str]" = os.environ) -> Path:
    """Where the log lives when ``GOAT_LOG_DIR`` is not set: ``$XDG_STATE_HOME/goat2/logs``, else
    ``~/.local/state/goat2/logs``. On disk that survives a reboot: the old default, ``/tmp/goat2/logs``, was
    emptied by every restart of the machine, taking days of measurements with it (2026-11-14)."""
    state = (environ.get("XDG_STATE_HOME") or "").strip()
    base = Path(state) if state else Path.home() / ".local" / "state"
    return base / "goat2" / "logs"


_LOG_DIR = Path(os.environ["GOAT_LOG_DIR"]) if os.environ.get("GOAT_LOG_DIR", "").strip() else default_log_dir()
_LOG_FILE = _LOG_DIR / "goat2.log"
_LOG_MAX_BYTES = int(os.environ.get("GOAT_LOG_MAX_BYTES", str(10 * 1024 * 1024)))
_LOG_BACKUP_COUNT = int(os.environ.get("GOAT_LOG_BACKUP_COUNT", "5"))

# Public alias so other modules (e.g. the get_recent_logs plugin) read the
# exact file this module writes — one source of truth for the log path.
LOG_FILE: Path = _LOG_FILE
_FMT = "%(asctime)s  %(name)-35s  %(levelname)-8s  %(message)s"
_DATE_FMT = "%Y-%m-%dT%H:%M:%S"
_QUIET_LIBS = ("httpx", "httpcore", "openai", "hpack", "h2", "asyncio", "markdown_it",
               "chromadb.utils.embedding_functions",  # 'No ONNX providers' DEBUG line on every embedding
               "chromadb.config")  # 'Starting component System/Posthog' DEBUG lines on every Chroma client start
_root_configured = False


def get_logger(name: str) -> logging.Logger:
    """
    Return a logger for ``name``, configuring the root logger on first call.

    Writes INFO+ to stdout and DEBUG+ to a rotating log file (default 10 MB,
    5 backups — override via GOAT_LOG_MAX_BYTES / GOAT_LOG_BACKUP_COUNT).
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
            _LOG_FILE, maxBytes=_LOG_MAX_BYTES, backupCount=_LOG_BACKUP_COUNT
        )
        fh.setFormatter(fmt)
        fh.setLevel(logging.DEBUG)
        root.addHandler(fh)

        for lib in _QUIET_LIBS:
            logging.getLogger(lib).setLevel(logging.WARNING)

        _root_configured = True

    return logging.getLogger(name)
