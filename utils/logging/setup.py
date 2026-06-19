"""Centralized logging setup for GOAT 2.0.

Wires every entry point (CLI, Telegram bot, MCP server, ad-hoc
scripts) to a rotating file handler at ``logs/goat2.log`` plus
a stderr handler. Idempotent — safe to call from any module
more than once; only the first call attaches handlers and
later calls just adjust the level.

DESIGN:
  - The file handler is a ``RotatingFileHandler`` capped at
    ``DEFAULT_MAX_BYTES`` (10 MB) with ``DEFAULT_BACKUP_COUNT``
    (5). Older files become ``goat2.log.1`` ... ``goat2.log.5``.
  - The directory is created on demand (``mkdir -p``).
  - Stderr is preserved at the same level so developers
    running ``python cli.py`` still see live output.
  - Re-running with a different ``level`` re-binds the level
    on the existing handlers without duplicating them, so
    ``configure_logging(level=DEBUG)`` after a normal start
    works as expected.
  - The ``goat2`` logger namespace gets the same level as
    root. Third-party loggers (``httpx``, ``telegram``,
    ``asyncio``) get WARNING to keep output readable.

USAGE (from an entry point):
    from utils.logging.setup import configure_logging
    configure_logging()                  # defaults
    configure_logging(level="DEBUG")     # change level at runtime
    configure_logging(file_path=Path("/var/log/goat.log"))  # override
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path
from typing import Final

__all__ = [
    "configure_logging",
    "DEFAULT_FORMAT",
    "DEFAULT_MAX_BYTES",
    "DEFAULT_BACKUP_COUNT",
    "DEFAULT_LOG_PATH",
]

DEFAULT_FORMAT:     Final[str] = (
    "%(asctime)s  %(name)-32s  %(levelname)-8s  %(message)s"
)
DEFAULT_MAX_BYTES:  Final[int] = 10 * 1024 * 1024  # 10 MB
DEFAULT_BACKUP_COUNT: Final[int] = 5
DEFAULT_LOG_PATH:   Final[Path] = Path("logs") / "goat2.log"

# Loggers known to be noisy at INFO. Throttled so the
# operator's view stays focused on goat2.* events.
_NOISY_LOGGERS: Final[tuple[str, ...]] = (
    "httpx", "httpcore", "apscheduler",
    "telegram.ext", "telegram.bot", "urllib3",
)

# Sentinel so we can tell "first call" from "subsequent" calls
# without holding module state that survives a reload.
_FILE_HANDLER_NAME: Final[str] = "goat2_file_handler"
_STDERR_HANDLER_NAME: Final[str] = "goat2_stderr_handler"


def _level_for(name: str) -> int:
    """Resolve a level name to a logging constant, defaulting to INFO."""
    return getattr(logging, name.upper(), logging.INFO)


def configure_logging(
    level: str | int = "INFO",
    file_path: Path | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
    fmt: str = DEFAULT_FORMAT,
) -> logging.Logger:
    """Attach file + stderr handlers to the root logger.

    Idempotent. On the first call a ``RotatingFileHandler``
    and a ``StreamHandler(sys.stderr)`` are created. On
    subsequent calls the existing handlers are reused and
    only their level + formatter are updated.

    Args:
        level: Root level — string (``"DEBUG"``) or logging constant.
        file_path: Where to write the log file. ``None`` →
            ``DEFAULT_LOG_PATH`` (relative to CWD).
        max_bytes: Per-file rotation size.
        backup_count: Number of rotated files to keep.
        fmt: Log record format string.

    Returns:
        The configured root logger (so callers can immediately
        ``root.info(...)`` if they want).
    """
    target = file_path or DEFAULT_LOG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(_level_for(level))
    formatter = logging.Formatter(fmt)

    # File handler — attach or update.
    file_h = _find_handler(root, _FILE_HANDLER_NAME)
    if file_h is None:
        file_h = logging.handlers.RotatingFileHandler(
            str(target), maxBytes=max_bytes, backupCount=backup_count,
            encoding="utf-8",
        )
        file_h.set_name(_FILE_HANDLER_NAME)
        root.addHandler(file_h)
    file_h.setLevel(_level_for(level))
    file_h.setFormatter(formatter)

    # Stderr handler — attach or update.
    stderr_h = _find_handler(root, _STDERR_HANDLER_NAME)
    if stderr_h is None:
        stderr_h = logging.StreamHandler(sys.stderr)
        stderr_h.set_name(_STDERR_HANDLER_NAME)
        root.addHandler(stderr_h)
    stderr_h.setLevel(_level_for(level))
    stderr_h.setFormatter(formatter)

    # Throttle noisy third-party loggers regardless of root level.
    for noisy in _NOISY_LOGGERS:
        logging.getLogger(noisy).setLevel(logging.WARNING)

    return root


def _find_handler(logger: logging.Logger, name: str) -> logging.Handler | None:
    """Return the first handler on ``logger`` with ``handler.name == name``."""
    for h in logger.handlers:
        if getattr(h, "name", None) == name:
            return h
    return None
