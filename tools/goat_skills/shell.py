"""goat_skills.shell — full-access shell tool, GOAT-only hot-reload plugin.

Executes via /bin/sh -c so pipes, redirects, &&, sudo, globs all work.
Timeout clamped to [1, 300]s; output truncated to 4 KB.
"""
from __future__ import annotations

import subprocess
import time
from typing import TYPE_CHECKING

from orchestrator.tools import ToolDefinition
from utils.logging.setup import get_logger

if TYPE_CHECKING:
    from registry.registry import ServiceRegistry

log = get_logger(__name__)
__all__ = ["build"]

_MAX_OUTPUT = 4000
_MAX_T, _MIN_T, _DEF_T = 300, 1, 30
_CMD_LIMIT = 120

_DESCRIPTION = (
    "Run a shell command with NO restrictions. Full host access — pipes, "
    "redirects, &&, sudo, rm, etc. all work. Timeout clamped to [1, 300]s. "
    "Output truncated to 4000 chars. Returns stdout+stderr plus '[exit N]' "
    "on non-zero exit. GOAT-only — NOT available to DAG agents."
)


def _fmt(stdout: str, stderr: str, returncode: int) -> str:
    parts = [p for p in (stdout, f"[stderr]\n{stderr}" if stderr else "") if p]
    if returncode != 0:
        parts.append(f"[exit {returncode}]")
    combined = "\n".join(parts)
    if len(combined) > _MAX_OUTPUT:
        omitted = len(combined) - _MAX_OUTPUT
        combined = combined[:_MAX_OUTPUT] + f"\n...[truncated {omitted} chars]"
    return combined or "(no output)"


def build(registry: "ServiceRegistry") -> list[ToolDefinition]:
    """Return the shell_run ToolDefinition (no registry deps needed)."""

    async def handler(command: str, timeout: int = _DEF_T, chat_id: str = "") -> str:
        if not isinstance(command, str) or not command.strip():
            return "ERROR: empty command"
        t = max(_MIN_T, min(int(timeout), _MAX_T))
        log.debug("shell_run: cmd=%r timeout=%ds", command[:_CMD_LIMIT], t)
        t0 = time.monotonic()
        try:
            r = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=t)
        except subprocess.TimeoutExpired:
            log.warning("shell_run: timeout %ds cmd=%r", t, command[:_CMD_LIMIT])
            return f"ERROR: command timed out after {t}s"
        except Exception as exc:
            log.exception("shell_run: error cmd=%r", command[:_CMD_LIMIT])
            return f"ERROR: {exc}"
        elapsed = time.monotonic() - t0
        if r.returncode != 0:
            log.warning("shell_run: exit=%d %.2fs cmd=%r", r.returncode, elapsed, command[:_CMD_LIMIT])
        else:
            log.info("shell_run: exit=0 %.2fs", elapsed)
        return _fmt(r.stdout or "", r.stderr or "", r.returncode)

    return [ToolDefinition(
        name="shell_run",
        description=_DESCRIPTION,
        parameters={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command (run via /bin/sh -c).",
                },
                "timeout": {
                    "type": "integer",
                    "description": f"Timeout seconds, clamped to [1, 300]. Default: {_DEF_T}.",
                    "default": _DEF_T,
                },
            },
            "required": ["command"],
        },
        handler=handler,
    )]
