"""Shell tool for DAG agents — restricted basic shell commands.

Provides SHELL ToolDefinition for safe, restricted shell execution.
Only basic read-only commands are allowed. No file modification
or dangerous ops.

SECURITY (BUG-026 fix):
  - The allow-list contains ONLY read-only commands. The previous
    version whitelisted ``python3``, ``python``, ``mkdir``,
    ``sed``, ``awk`` — all of which can mutate state. A model
    could run ``python -c "import os; os.system('...')"`` or
    ``sed -i`` to modify files, defeating the read-only guarantee.
  - Argument paths are validated: any path argument must resolve
    under ``$GOAT_WORKSPACE`` (or $HOME if unset). Reading
    ``/etc/passwd`` or ``~/.ssh/id_rsa`` is blocked.
  - Error messages do NOT enumerate the full allow-list — a
    prompt-injected LLM must not be able to enumerate every
    permitted command from a rejection message.
  - No shell features: pipes, redirects, command chaining, globs
    with command substitution, etc. are blocked at the validator.
"""
from __future__ import annotations

import logging
import os
import posixpath
import subprocess
from typing import TYPE_CHECKING, Final

from tools._make_tool import make_tool

if TYPE_CHECKING:
    from agents.base_agent import ToolDefinition

log = logging.getLogger("goat2.tools.system.shell")

__all__ = ["SHELL", "ALLOWED_COMMANDS"]


# Whitelist of read-only commands. BUG-026: removed python3, python,
# mkdir, sed, awk, touch, tee — all of which can mutate state.
ALLOWED_COMMANDS: Final[frozenset[str]] = frozenset({
    "ls", "pwd", "cat", "head", "tail", "grep", "echo",
    "find", "wc", "du", "df", "ps", "date", "whoami",
    "hostname", "uname", "sort", "uniq", "tr", "cut", "join",
    "basename", "dirname", "readlink", "file", "stat",
    "lsblk", "mount", "free", "uptime", "nproc", "id",
    "groups", "which", "env", "printenv",
})


# Block any shell feature that could be used to chain commands,
# expand variables, redirect I/O, or escape quoting.
_BLOCKED_PATTERNS: Final[frozenset[str]] = frozenset({
    "|", ";", "&&", "||", "`", "\\", "\n", "$",
    "&", "!", "?", "[", "]", "{", "}", "~", "'", "\"",
    ">", "<", "*", "(", ")",
})


# Argument substrings that indicate file-mutation or network
# exfiltration intent. Substring match — no regex.
_BLOCKED_ARG_SUBSTRINGS: Final[frozenset[str]] = frozenset({
    "rm", "cp", "mv", "dd", "sudo", "wget", "curl", "nc", "ssh",
    "chmod", "chown", "touch", "tee", "ln", "mkfifo", "mknod",
    "python", "perl", "ruby", "node", "sh", "bash",
    "--no-check-certificate", "--insecure", "eval", "exec",
})


# Maximum shell-tool timeout. The handler clamps any caller value
# down to this ceiling so a prompt-injected model cannot run a
# command forever.
_MAX_TIMEOUT_S: Final[int] = 60
_DEFAULT_TIMEOUT_S: Final[int] = 30


def _workspace_root() -> str:
    """Return the resolved workspace root for path checks.

    Order: ``$GOAT_WORKSPACE`` > user home. The root is normalised
    via ``realpath`` so symlinks can't smuggle paths outside.
    """
    root = os.environ.get("GOAT_WORKSPACE") or os.path.expanduser("~")
    try:
        return os.path.realpath(root)
    except OSError:
        return root


def _is_path_argument_safe(arg: str) -> bool:
    """True when ``arg`` is either a flag (starts with ``-``), a
    pure non-path option (no ``/``), or resolves under the
    workspace root.

    Defensive: any path containing ``..`` is rejected outright
    so a model cannot reference ``../../etc/passwd``.
    """
    if not arg or not arg.strip():
        return True  # empty arg — not a path
    if arg.startswith("-"):
        return True  # flag
    # Pure numeric or simple value — treat as non-path.
    if "/" not in arg and "\\" not in arg:
        return True
    # Path with traversal tokens — reject.
    if ".." in posixpath.normpath(arg).split("/"):
        return False
    # Resolve and check containment under workspace root.
    try:
        # Use realpath only when the file exists; for non-existent
        # paths, just check the textual prefix (best-effort).
        if os.path.exists(arg):
            resolved = os.path.realpath(arg)
        else:
            resolved = posixpath.normpath(os.path.abspath(arg))
    except (OSError, ValueError):
        return False
    root = _workspace_root()
    # Common-prefix check; allow the root itself.
    return resolved == root or resolved.startswith(root + os.sep)


def _validate_command(cmd: str, args: str) -> str | None:
    """Validate command and args against security rules.

    Returns None if valid, or error string if blocked.
    """
    if not cmd:
        return "ERROR: empty command"
    if cmd not in ALLOWED_COMMANDS:
        return f"ERROR: command {cmd!r} not allowed"
    # Check for blocked patterns in command and args.
    for pattern in _BLOCKED_PATTERNS:
        if pattern in cmd:
            return f"ERROR: blocked pattern {pattern!r}"
        if pattern in args:
            return f"ERROR: blocked pattern {pattern!r} in arguments"
    # Check for dangerous argument substrings.
    args_lc = args.lower()
    for blocked in _BLOCKED_ARG_SUBSTRINGS:
        if blocked in args_lc:
            return f"ERROR: blocked argument {blocked!r}"
    # Check that every path-looking argument stays under the
    # workspace root.
    for token in args.split():
        if not _is_path_argument_safe(token):
            return f"ERROR: argument {token!r} is outside the workspace"
    return None


async def _shell_handler(
    command: str,
    timeout: int = _DEFAULT_TIMEOUT_S,
) -> str:
    """Execute a basic read-only shell command.

    Args:
        command: Shell command to execute (validated against
            allow-list and path-prefix rules).
        timeout: Command timeout in seconds (default 30, hard cap 60).

    Returns:
        Command output or error message. Never raises.
    """
    log.debug("shell: command=%r timeout=%d", command[:80], timeout)
    parts = command.strip().split()
    if not parts:
        log.warning("shell: empty command")
        return "ERROR: empty command"

    cmd = parts[0]
    args = " ".join(parts[1:])

    error = _validate_command(cmd, args)
    if error:
        log.warning("shell: validation failed for %r: %s", command[:80], error)
        return error

    # Clamp timeout so a model can't run a command forever.
    timeout = max(1, min(int(timeout or _DEFAULT_TIMEOUT_S), _MAX_TIMEOUT_S))

    safe_cmd = [cmd] + parts[1:]
    try:
        result = subprocess.run(
            safe_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=_workspace_root(),
        )
        output = result.stdout
        if result.stderr:
            output += f"\n[stderr]: {result.stderr}"
        return output.strip() if output else f"{cmd}: no output"
    except subprocess.TimeoutExpired:
        log.warning("shell: command timed out after %ds: %r", timeout, command[:80])
        return f"ERROR: command timed out after {timeout}s"
    except FileNotFoundError:
        log.warning("shell: command not found: %r", cmd)
        return f"ERROR: command not found: {cmd}"
    except Exception as exc:
        log.exception("shell: unexpected error: %r", command[:80])
        return f"ERROR: {exc}"


SHELL = make_tool(
    name="shell",
    description=(
        "Execute basic read-only shell commands (ls, pwd, cat, head, "
        "tail, grep, echo, find, wc, du, df, ps, date, whoami, "
        "hostname, uname). DAG agents only. No file modification, "
        "no dangerous ops, no shell features (pipes, redirects, "
        "command substitution are blocked at the validator)."
    ),
    parameters={
        "type": "object",
        "required": ["command"],
        "properties": {
            "command": {
                "type": "string",
                "description": (
                    "Command to execute (validated; restricted to "
                    "read-only ops, all file arguments must stay "
                    "under the workspace root)."
                ),
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (default 30, max 60).",
                "default": _DEFAULT_TIMEOUT_S,
            },
        },
    },
    handler=_shell_handler,
)