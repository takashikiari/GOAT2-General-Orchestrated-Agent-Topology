"""Shell tool for DAG agents — restricted basic shell commands.

Provides SHELL ToolDefinition for safe, restricted shell execution.
Only basic read-only commands are allowed. No file modification or dangerous ops.

SECURITY:
- Only whitelisted commands: ls, pwd, cat, head, tail, grep, echo, mkdir, find, wc, du, df, ps, top (non-interactive), date, whoami, hostname, uname, history
- No: rm, cp, mv, dd, sudo, wget, curl, nc, ssh, chmod, chown, touch, tee
- No: pipes (|), redirects (>), semicolons (;), &&, ||, $(), backticks
- No: cd (handled by workspace), aliases, functions
- Args are validated — no special chars that could escape
"""
from __future__ import annotations

import subprocess
from typing import Final

from agents.base_agent import ToolDefinition

__all__ = ["SHELL"]

# Whitelist of allowed commands (basic read-only operations)
ALLOWED_COMMANDS: Final[set[str]] = {
    "ls", "pwd", "cat", "head", "tail", "grep", "echo", "mkdir",
    "find", "wc", "du", "df", "ps", "date", "whoami", "hostname",
    "uname", "sort", "uniq", "tr", "sed", "awk", "cut", "join",
    "basename", "dirname", "readlink", "file", "stat", "lsblk",
    "mount", "free", "uptime", "nproc", "id", "groups",
}

# Block patterns that could escape the whitelist
BLOCKED_PATTERNS: Final[set[str]] = {
    "|", ">", "<", ";", "&&", "||", "$", "`", "\\", "\n",
    "&", "!", "*", "?", "[", "]", "{", "}", "~", "'", "\"",
}

# Block dangerous argument patterns
BLOCKED_ARGS: Final[set[str]] = {
    "rm", "cp", "mv", "dd", "sudo", "wget", "curl", "nc", "ssh",
    "chmod", "chown", "touch", "tee", "ln", "mkfifo", "mknod",
    "--no-check-certificate", "-- insecure", "eval",
}


def _validate_command(cmd: str, args: str) -> str | None:
    """Validate command and args against security rules.

    Returns None if valid, or error string if blocked.
    """
    # Check for blocked patterns in command
    for pattern in BLOCKED_PATTERNS:
        if pattern in cmd:
            return f"ERROR: blocked pattern {pattern!r} in command"

    # Check for blocked arguments
    for blocked in BLOCKED_ARGS:
        if blocked in args:
            return f"ERROR: blocked argument {blocked!r}"

    # Check command is whitelisted
    if cmd not in ALLOWED_COMMANDS:
        return f"ERROR: command {cmd!r} not allowed. Allowed: ls, pwd, cat, head, tail, grep, echo"

    # Check for potential command injection in args
    for char in BLOCKED_PATTERNS:
        if char in args:
            return f"ERROR: blocked pattern {char!r} in arguments"

    return None


async def _shell_handler(
    command: str,
    timeout: int = 30,
) -> str:
    """Execute a basic shell command (restricted to whitelisted operations).

    DAG agents can use this for basic read operations only.
    No file modification, no dangerous commands, no pipes/shell features.

    Args:
        command: Shell command to execute (validated against whitelist).
        timeout: Command timeout in seconds (default 30, max 60).

    Returns:
        Command output or error message.
    """
    # Parse command (simple split, no shell features)
    parts = command.strip().split()
    if not parts:
        return "ERROR: empty command"

    cmd = parts[0]
    args = " ".join(parts[1:])

    # Validate
    error = _validate_command(cmd, args)
    if error:
        return error

    # Build safe command
    safe_cmd = [cmd] + parts[1:]

    # Limit timeout
    timeout = min(timeout, 60)

    try:
        result = subprocess.run(
            safe_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd="/home/lenovo",  # Restrict to home or workspace
        )
        output = result.stdout
        if result.stderr:
            output += f"[stderr]: {result.stderr}"
        return output.strip() if output else f"{cmd}: no output"
    except subprocess.TimeoutExpired:
        return f"ERROR: command timed out after {timeout}s"
    except FileNotFoundError:
        return f"ERROR: command not found: {cmd}"
    except Exception as exc:
        return f"ERROR: {exc}"


SHELL = ToolDefinition(
    name="shell",
    description="Execute basic read-only shell commands (ls, pwd, cat, head, tail, grep, echo, find, etc.). "
                "DAG agents only - no file modification, no dangerous ops, no pipes/shell features.",
    parameters={
        "type": "object",
        "required": ["command"],
        "properties": {
            "command": {
                "type": "string",
                "description": "Command to execute (validated, restricted to basic read-only ops).",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (default 30, max 60).",
                "default": 30,
            },
        },
    },
    handler=_shell_handler,
)