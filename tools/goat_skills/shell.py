"""Unrestricted shell tool for GOAT — TOOL 7 (goat_skills).

Unlike ``tools.system.shell_tool.SHELL`` (which is whitelisted and
read-only for DAG agents), ``SHELL_RUN`` gives GOAT full shell access
in conversational mode. No command is blocked, no pattern is
forbidden — the user is talking to GOAT interactively and has
implicitly authorised it to drive the host.

SECURITY MODEL:
  - Wired ONLY into supervisor.identity.direct_response() (GOAT).
  - DAG agents cannot invoke this tool (they use the restricted
    ``SHELL`` in tools/system/shell_tool.py).
  - Timeout is clamped to [1, 300] seconds to bound runaway commands.
  - Output is truncated to 4000 characters to keep LLM context manageable.

LIBRARY: stdlib ``subprocess`` only — no external dependencies, no
graceful-fallback path needed (this tool is always available).
"""
from __future__ import annotations

import logging
import subprocess
import time
from typing import TYPE_CHECKING

from tools._make_tool import make_tool

if TYPE_CHECKING:
    from agents.base_agent import ToolDefinition

log = logging.getLogger("goat2.tools.goat_skills.shell")

__all__ = ["SHELL_RUN", "run_shell"]

# Output truncation limit for the combined stdout+stderr payload.
_MAX_OUTPUT_CHARS: int = 4000
# Hard cap on the user-supplied timeout (seconds).
_MAX_TIMEOUT_S: int = 300
# Hard floor on the user-supplied timeout (seconds).
_MIN_TIMEOUT_S: int = 1
# Default timeout when the caller does not provide one.
_DEFAULT_TIMEOUT_S: int = 30
# Length at which we truncate command text in log lines.
_CMD_LOG_LIMIT: int = 120


def _format_output(stdout: str, stderr: str, returncode: int) -> str:
    """Combine stdout + stderr and truncate to ``_MAX_OUTPUT_CHARS``."""
    parts: list[str] = []
    if stdout:
        parts.append(stdout)
    if stderr:
        parts.append(f"[stderr]\n{stderr}")
    if returncode != 0:
        parts.append(f"[exit {returncode}]")
    combined = "\n".join(p for p in parts if p)
    if len(combined) > _MAX_OUTPUT_CHARS:
        omitted = len(combined) - _MAX_OUTPUT_CHARS
        combined = combined[:_MAX_OUTPUT_CHARS] + f"\n...[truncated {omitted} chars]"
    return combined or "(no output)"


def run_shell(command: str, timeout: int = _DEFAULT_TIMEOUT_S) -> str:
    """Execute a shell command using subprocess and return stdout/stderr.

    This is a synchronous convenience wrapper around ``subprocess.run``.
    It runs the command via ``/bin/sh -c`` so pipes, redirects, globs,
    and ``&&`` all work.

    Args:
        command: Shell command string to execute.
        timeout: Maximum wall-clock seconds to wait (clamped to
                 ``[_MIN_TIMEOUT_S, _MAX_TIMEOUT_S]``).

    Returns:
        Combined stdout + stderr (truncated to ~4 KB), with an
        ``[exit N]`` trailer on non-zero return codes. Returns an
        ``ERROR:`` string on failure.
    """
    if not isinstance(command, str) or not command.strip():
        log.warning("run_shell: empty command")
        return "ERROR: empty command"
    safe_timeout = max(_MIN_TIMEOUT_S, min(int(timeout), _MAX_TIMEOUT_S))
    log.debug(
        "run_shell: command=%r timeout=%ds",
        command[:_CMD_LOG_LIMIT],
        safe_timeout,
    )
    t0 = time.monotonic()
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=safe_timeout,
        )
    except subprocess.TimeoutExpired:
        log.warning("run_shell: timed out after %ds for %r", safe_timeout, command[:_CMD_LOG_LIMIT])
        return f"ERROR: command timed out after {safe_timeout}s"
    except Exception as exc:
        log.exception("run_shell: unexpected error for %r", command[:_CMD_LOG_LIMIT])
        return f"ERROR: shell_run failed: {exc}"
    duration_s = time.monotonic() - t0
    if result.returncode != 0:
        log.warning(
            "run_shell: non-zero exit %d in %.2fs for %r",
            result.returncode, duration_s, command[:_CMD_LOG_LIMIT],
        )
    else:
        log.info("run_shell: exit=0 in %.2fs", duration_s)
    return _format_output(result.stdout or "", result.stderr or "", result.returncode)


# ── TOOL 7: shell_run(command, timeout=30) ──────────────────────────────

async def _shell_run_handler(command: str, timeout: int = _DEFAULT_TIMEOUT_S) -> str:
    """Run a shell command with full access (GOAT-only).

    Args:
        command: Shell command string. Executed via ``/bin/sh -c`` so
                 pipes, redirects, globs, and ``&&`` all work.
        timeout: Maximum wall-clock seconds to wait. Clamped to
                 ``[_MIN_TIMEOUT_S, _MAX_TIMEOUT_S]``.

    Returns:
        Combined stdout + stderr (truncated to ~4 KB), with an
        ``[exit N]`` trailer on non-zero return codes. Errors return
        an ``ERROR:`` string.
    """
    if not isinstance(command, str) or not command.strip():
        log.warning("shell_run: empty command")
        return "ERROR: empty command"
    safe_timeout = max(_MIN_TIMEOUT_S, min(int(timeout), _MAX_TIMEOUT_S))
    log.debug(
        "shell_run: command=%r timeout=%ds",
        command[:_CMD_LOG_LIMIT],
        safe_timeout,
    )
    t0 = time.monotonic()
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=safe_timeout,
        )
    except subprocess.TimeoutExpired:
        log.warning("shell_run: timed out after %ds for %r", safe_timeout, command[:_CMD_LOG_LIMIT])
        return f"ERROR: command timed out after {safe_timeout}s"
    except Exception as exc:
        log.exception("shell_run: unexpected error for %r", command[:_CMD_LOG_LIMIT])
        return f"ERROR: shell_run failed: {exc}"
    duration_s = time.monotonic() - t0
    if result.returncode != 0:
        log.warning(
            "shell_run: non-zero exit %d in %.2fs for %r",
            result.returncode, duration_s, command[:_CMD_LOG_LIMIT],
        )
    else:
        log.info("shell_run: exit=0 in %.2fs", duration_s)
    return _format_output(result.stdout or "", result.stderr or "", result.returncode)


SHELL_RUN = make_tool(
    name="shell_run",
    description=(
        "Run a shell command with NO restrictions. Full host access — "
        "pipes, redirects, &&, sudo, rm, etc. all work. This is the "
        "GOAT-only counterpart to the DAG-agent 'shell' tool (which is "
        "whitelisted and read-only). Timeout is clamped to [1, 300] "
        "seconds. Output is truncated to 4000 chars. Returns the "
        "combined stdout+stderr plus an '[exit N]' trailer on non-zero "
        "exit codes. GOAT-only — NOT available to DAG agents."
    ),
    parameters={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Shell command to execute (run via /bin/sh -c).",
            },
            "timeout": {
                "type": "integer",
                "description": (
                    "Timeout in seconds. Clamped to [1, 300]. "
                    f"Default: {_DEFAULT_TIMEOUT_S}."
                ),
                "default": _DEFAULT_TIMEOUT_S,
            },
        },
        "required": ["command"],
    },
    handler=_shell_run_handler,
)


# ── Main test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Quick smoke test for run_shell()
    print("=== Testing run_shell() ===")

    # Test 1: simple echo
    result = run_shell("echo 'Hello from run_shell!'")
    print(f"Test 1 (echo): {result!r}")

    # Test 2: pipeline
    result = run_shell("echo 'hello world' | wc -w")
    print(f"Test 2 (pipeline): {result!r}")

    # Test 3: non-zero exit
    result = run_shell("false")
    print(f"Test 3 (false): {result!r}")

    # Test 4: empty command
    result = run_shell("")
    print(f"Test 4 (empty): {result!r}")

    # Test 5: stderr capture
    result = run_shell("echo 'stdout line'; echo 'stderr line' >&2")
    print(f"Test 5 (stderr): {result!r}")

    print("\n=== All tests completed ===")
