"""Error-fallback reply builder — render the universal ``_empty_result`` body.

Extracted from ``supervisor.py`` to keep that file under the 260-line
ceiling. Pure orchestration: reads ``config/goat.toml [errors]``,
formats the visible text, builds the ``SupervisorResult``.

USAGE:
    from supervisor.errors_fallback import empty_error_result

    result = empty_error_result(supervisor, intent=intent, t0=t0, err=exc)

WHY A SEPARATE MODULE:
    The original inline body was a long block buried in the
    supervisor class. Splitting it keeps the supervisor focused on
    orchestration while this module owns the error-formatting
    policy (which text template to use, how to truncate, whether
    to include the exception type) — all driven by config so
    operators can tune the message without code changes.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

log = logging.getLogger("goat2.supervisor.errors_fallback")

__all__ = ["empty_error_result", "load_errors_config"]


if TYPE_CHECKING:
    from supervisor.supervisor import GoatSupervisor
    from supervisor.types import SupervisorResult


_DEFAULT_TEMPLATE: str = (
    "GOAT hit an error: {error_type}: {error_message}. "
    "Please retry or simplify your request."
)
_DEFAULT_MAX_CHARS: int = 200


def load_errors_config() -> dict[str, object]:
    """Read ``[errors]`` from config/goat.toml with safe defaults.

    Returns a dict with three keys: ``fallback_template`` (str),
    ``include_type`` (bool), ``max_chars`` (int). Falls back to
    module defaults when the section is missing or unparseable.
    """
    out: dict[str, object] = {
        "fallback_template": _DEFAULT_TEMPLATE,
        "include_type":      True,
        "max_chars":         _DEFAULT_MAX_CHARS,
    }
    try:
        from config.modular_loader import load_goat_config
        section = (load_goat_config() or {}).get("errors", {}) or {}
        raw_tpl = section.get("fallback_template")
        if isinstance(raw_tpl, str) and raw_tpl.strip():
            out["fallback_template"] = raw_tpl
        raw_inc = section.get("include_type")
        if isinstance(raw_inc, bool):
            out["include_type"] = raw_inc
        raw_max = section.get("max_chars")
        try:
            if raw_max is not None:
                out["max_chars"] = max(1, int(raw_max))
        except (TypeError, ValueError):
            log.debug("errors_fallback: max_chars=%r not int — using default", raw_max)
    except Exception as exc:  # noqa: BLE001 — config load is best-effort
        log.debug("errors_fallback: goat.toml [errors] load skipped: %s", exc)
    return out


def _render_summary(err: BaseException, cfg: dict[str, object]) -> str:
    """Format the visible fallback text for one exception.

    Args:
        err: The caught exception.
        cfg: The dict returned by ``load_errors_config``.

    Returns:
        A non-empty string that embeds the real exception type + message.
    """
    template     = str(cfg.get("fallback_template") or _DEFAULT_TEMPLATE)
    include_type = bool(cfg.get("include_type", True))
    max_chars    = int(cfg.get("max_chars", _DEFAULT_MAX_CHARS) or _DEFAULT_MAX_CHARS)
    try:
        err_str = str(err)[:max_chars]
    except Exception:  # noqa: BLE001
        err_str = "<unprintable error>"
    err_type = type(err).__name__ if include_type else ""
    summary = template.format(error_type=err_type, error_message=err_str).strip()
    if err_type and err_type not in summary:
        # Template didn't include the type — prepend defensively so the
        # operator always sees what class of error occurred.
        summary = f"{err_type}: {summary}"
    return summary


def empty_error_result(
    supervisor: "GoatSupervisor",
    intent: str,
    t0: float,
    err: BaseException,
) -> "SupervisorResult":
    """Build the universal ``SupervisorResult`` for an unhandled error.

    The kernel must always respond — that's the rule that motivates the
    try/except in ``GoatSupervisor.run``. But responding honestly means
    the user / MCP ``diagnose_turn`` can tell what went wrong without
    spelunking through debug logs.

    Side effects:
        - Logs at WARNING with the exception class name + message
          (was previously DEBUG, so recurring failures went unnoticed).

    Args:
        supervisor: The live GoatSupervisor (only used for ``session_id``).
        intent: The raw user intent for the failed turn.
        t0: Monotonic start time (for duration accounting).
        err: The exception that was caught.

    Returns:
        A ``SupervisorResult`` whose summary embeds the real error and
        whose ``sources['conv']`` is ``"error"`` so downstream
        channels (Telegram, CLI) can render error replies distinctly
        from normal chat.
    """
    log.warning("empty_error_result: GOAT hit an error: %s: %s", type(err).__name__, err)
    cfg = load_errors_config()
    summary = _render_summary(err, cfg)
    return supervisor._build_result(
        intent=intent, t0=t0,
        summary=summary,
        source="error", session_id="",
    )