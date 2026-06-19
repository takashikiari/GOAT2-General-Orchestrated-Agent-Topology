"""Tests for BUG-026 fix: tighten the shell_tool whitelist.

The previous shell_tool.py had several security issues that the
audit surfaced:

  1. ``python3`` and ``python`` were whitelisted. A model could
     run arbitrary Python (``python -c "import os; os.system('rm -rf /')"``)
     — defeating the entire "DAG agents are read-only" guarantee.
  2. ``mkdir`` was whitelisted. The agent could create directories
     outside the workspace.
  3. ``sed`` and ``awk`` can modify files in place (``sed -i``).
  4. The error messages revealed the full allow-list, helping an
     attacker enumerate permitted commands.
  5. There was no path-prefix check on arguments — a model could
     read any file on disk (``cat /etc/passwd``).

The fix:
  - Remove ``python3`` / ``python`` / ``sed`` / ``awk`` / ``mkdir``
    from the whitelist. The remaining commands are genuinely
    read-only.
  - Add a path-prefix check: any file argument must resolve under
    the working directory (``GOAT_WORKSPACE`` or HOME). Reading
    ``/etc/passwd`` or ``~/.ssh/id_rsa`` is now blocked.
  - Sanitise error messages: don't leak the full allow-list.
"""
from __future__ import annotations

import pytest

from tools.system.shell_tool import (
    ALLOWED_COMMANDS,
    SHELL,
    _shell_handler,
    _validate_command,
)


# ── Whitelist contents ─────────────────────────────────────────────────────


def test_python_interpreters_not_in_whitelist():
    """The shell tool must not allow arbitrary Python execution —
    a model could run any Python code, defeating the read-only
    guarantee."""
    assert "python" not in ALLOWED_COMMANDS
    assert "python3" not in ALLOWED_COMMANDS


def test_mkdir_not_in_whitelist():
    """``mkdir`` is a file-mutation command, not read-only."""
    assert "mkdir" not in ALLOWED_COMMANDS


def test_in_place_editors_not_in_whitelist():
    """``sed -i`` and ``awk`` can modify files in place."""
    assert "sed" not in ALLOWED_COMMANDS
    assert "awk" not in ALLOWED_COMMANDS


def test_whitelist_only_contains_genuinely_read_only_commands():
    """Sanity: every command in the whitelist must be one of the
    well-known read-only utilities."""
    allowed_readonly = {
        "ls", "pwd", "cat", "head", "tail", "grep", "echo",
        "find", "wc", "du", "df", "ps", "date", "whoami",
        "hostname", "uname", "sort", "uniq", "tr", "cut", "join",
        "basename", "dirname", "readlink", "file", "stat",
        "lsblk", "mount", "free", "uptime", "nproc", "id",
        "groups", "which", "env", "printenv",
    }
    extra = ALLOWED_COMMANDS - allowed_readonly
    assert not extra, (
        f"whitelist contains commands that are NOT in the canonical "
        f"read-only set: {sorted(extra)}"
    )


# ── Path-prefix check on arguments ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_shell_handler_blocks_path_outside_workspace(monkeypatch):
    """Reading /etc/passwd or ~/.ssh/id_rsa must be blocked.

    We don't actually need to execute the command — the handler
    returns 'ERROR: ...' before subprocess.run when the path
    check fails."""
    monkeypatch.setenv("GOAT_WORKSPACE", "/home/user/workspace")
    out = await _shell_handler(command="cat /etc/passwd")
    assert out.startswith("ERROR"), out
    assert "outside" in out.lower() or "blocked" in out.lower()


@pytest.mark.asyncio
async def test_shell_handler_blocks_ssh_directory_access(monkeypatch):
    monkeypatch.setenv("GOAT_WORKSPACE", "/home/user/workspace")
    out = await _shell_handler(command="ls ~/.ssh")
    assert out.startswith("ERROR"), out


# ── Validation function: no full-allowlist leak in errors ──────────────────


def test_validate_command_does_not_leak_full_allowlist():
    """When an unknown command is rejected, the error must not
    enumerate the entire allow-list (information disclosure to
    a prompt-injected LLM)."""
    err = _validate_command("evil", "arg")
    assert err is not None
    # Must not include the full allow-list of 30+ commands.
    # A safe rejection message references only the offending command.
    assert "evil" in err
    # And the rejection does not list 20+ allowed commands.
    assert err.count("'") < 6, (
        f"validation error leaks too much allow-list info: {err!r}"
    )


def test_validate_command_blocks_dangerous_argument_words():
    """Words like 'rm', 'sudo', 'curl', 'wget' must never appear
    in the allowed args — they're flagged by substring match."""
    err = _validate_command("ls", "rm -rf /")
    assert err is not None
    assert "rm" in err or "blocked" in err.lower()


# ── Tool surface ───────────────────────────────────────────────────────────


def test_shell_tool_description_does_not_mention_python():
    """The tool description tells the model which commands are
    available. It must not advertise Python — the LLM might
    try it."""
    desc = SHELL.description.lower()
    assert "python" not in desc


def test_shell_tool_registered():
    """SHELL must be a ToolDefinition with a handler."""
    assert SHELL.name == "shell"
    assert callable(SHELL.handler)
