"""Logging policy — standardise the level for caught exceptions.

BUG-030: the codebase used different log levels for the same
event — some ``except Exception`` clauses logged at ``debug``
(swallowed silently), others at ``warning`` (visible to
operators). This is a maintainability and observability
hazard: a recurring failure is either invisible or noisy,
depending on which developer wrote the ``except`` block.

The policy (see ``docs/logging_policy.md``):

  - A caught exception that the caller can RECOVER from
    silently (e.g. a fallback path exists and is logged
    separately) → ``log.debug``.
  - A caught exception that means the caller's expected
    behaviour could not be delivered → ``log.warning``
    (operator-visible).
  - An uncaught exception that crashes a turn → ``log.error``
    or ``log.exception`` (with stack trace).

To enforce this, modules should:

  1. Use ``log.exception(...)`` instead of ``log.debug(...)`` /
     ``log.warning(...)`` when logging an exception with the
     full stack trace. ``log.exception`` is always at
     ``ERROR`` level and includes the traceback automatically.
  2. Use ``log.debug`` only for *expected* failure modes where
     a fallback is in place and the failure is part of normal
     operation.
  3. Use ``log.warning`` for *unexpected* failure modes where
     the operator should investigate.

The helper below (``safe_log_exception``) wraps a log call so
the message is always at WARNING level (operator-visible) when
the exception is unexpected. Use it for any ``except Exception``
block where the failure is NOT part of the normal flow.

USAGE:
    try:
        await do_thing()
    except Exception as exc:
        safe_log_exception(log, "do_thing failed", exc)
        # ... handle / fallback ...
"""
from __future__ import annotations

import logging
from typing import Any

__all__ = ["safe_log_exception", "log_unexpected_failure", "EXPECTED_FAILURE_HINT"]


# A short, recognisable prefix so the operator can grep
# through logs for "unexpected" failures.
EXPECTED_FAILURE_HINT: str = "expected failure"


def safe_log_exception(
    log: logging.Logger,
    msg: str,
    exc: BaseException,
    *,
    level: int = logging.WARNING,
    include_traceback: bool = True,
) -> None:
    """Log an exception with a consistent policy.

    Defaults to ``WARNING`` level (operator-visible) and
    includes the stack trace. Use ``level=logging.DEBUG`` for
    expected-failure paths.

    Args:
        log: The module-specific logger.
        msg: Human-readable context (what the caller was doing).
        exc: The caught exception.
        level: Logging level (default WARNING).
        include_traceback: When True, the full traceback is
            included via ``log.exception`` semantics. When False,
            only the message + str(exc) are logged.
    """
    if include_traceback and level >= logging.WARNING:
        # log.exception is always at ERROR level — to keep the
        # requested level (e.g. WARNING) but still attach the
        # traceback, we manually format the message.
        if level >= logging.ERROR:
            log.exception("%s: %s", msg, exc)
        else:
            log.log(level, "%s: %s", msg, exc, exc_info=True)
    else:
        log.log(level, "%s: %s", msg, exc)


def log_unexpected_failure(
    log: logging.Logger,
    context_msg: str,
    exc: BaseException,
) -> None:
    """Log a failure that the operator should investigate.

    Always at WARNING level with a stack trace. Use this for
    exceptions caught inside defensive ``try/except`` blocks
    where the failure is not part of normal operation but the
    caller wants to keep the turn alive (e.g. a single tool
    failed; the supervisor still has other tools to try).

    Args:
        log: The module-specific logger.
        context_msg: What the caller was doing when it failed.
        exc: The caught exception.
    """
    log.warning(
        "UNEXPECTED — %s: %s: %s",
        context_msg, type(exc).__name__, exc,
        exc_info=True,
    )