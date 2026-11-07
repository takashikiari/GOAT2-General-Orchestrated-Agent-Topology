"""goat_skills.petvoyageuk_review — read-only reviewer access to the
PetVoyageUK repo on laris. The key is a normal login key (the old server-side
wrapper was lost with the Ubuntu box), so every command is checked client-side by
tools.repo_git.validate and only a quoted `git -C <repo> ...` ever reaches SSH.
GOAT-only hot-reload plugin.
"""
from __future__ import annotations

import asyncio
import shlex
import subprocess
import time
from typing import TYPE_CHECKING

from orchestrator.tools import ToolDefinition
from tools import repo_git as rg
from utils.logging.setup import get_logger

if TYPE_CHECKING:
    from registry.registry import ServiceRegistry

log = get_logger(__name__)
__all__ = ["build"]

_SSH_KEY = "/home/lenovo/.ssh/id_ed25519"
_HOST = "laris@100.106.196.112"

_REPO = "/home/laris/petvoyageuk"
_TIMEOUT = 20

# The remote box's goat2-ssh-wrapper enforces a hard 2s-between-calls rate
# limit on this key. The model routinely calls review_git/coord_inbox/
# coord_send back-to-back within the same tool round (e.g. retrying a
# command right after a denial, or chaining a log lookup with a coord
# check) - each denied call just returns a terse "[stderr] ... rate limit"
# string as the tool result, which burns an iteration without giving the
# model anything to act on, and a full round of these can exhaust
# AGENTIC_MAX_ITERATIONS with nothing useful gathered, forcing a synthesis
# call with empty tool output - the empty-reply bug this was traced to
# (2026-08-25). Serializing calls here with a >2s minimum gap means the
# model never sees the denial in the first place.
_MIN_CALL_GAP_SECONDS = 2.2
_last_call_at = 0.0
_call_lock = asyncio.Lock()


async def _throttle() -> None:
    global _last_call_at
    async with _call_lock:
        wait = _MIN_CALL_GAP_SECONDS - (time.monotonic() - _last_call_at)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_call_at = time.monotonic()


def _run_ssh(remote_cmd: str) -> str:
    try:
        r = subprocess.run(
            ["ssh", "-i", _SSH_KEY, "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", _HOST, remote_cmd],
            capture_output=True, text=True, timeout=_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return "ERROR: ssh timed out"
    except Exception as exc:
        log.exception("petvoyageuk_review: ssh error cmd=%r", remote_cmd[:200])
        return f"ERROR: {exc}"
    out = (r.stdout or "") + (f"\n[stderr] {r.stderr}" if r.stderr else "")
    return out.strip() or "(no output)"


def build(registry: "ServiceRegistry") -> list[ToolDefinition]:
    # chat_id is injected into every tool handler's kwargs by the
    # orchestrator's tool round (see Orchestrator._call_tool) - accepted
    # and ignored here, same convention as tools/memory_tools.py etc.,
    # since this plugin talks to a fixed external channel, not per-chat state.
    async def review_git(command: str, chat_id: str = "") -> str:
        log.info("petvoyageuk_review_git: %r", command[:200])
        # No server-side wrapper any more: refuse anything that is not one
        # read-only git command, then send only a fully quoted git argv.
        try:
            args = rg.validate(command)
        except ValueError as exc:
            return f"REFUZAT: {exc}"
        cmd = "GIT_PAGER=cat GIT_TERMINAL_PROMPT=0 " + " ".join(
            shlex.quote(a) for a in ["git", *rg.GIT_SAFE, "-C", _REPO, *args])
        await _throttle()
        return await asyncio.to_thread(_run_ssh, cmd)

    return [
        ToolDefinition(
            name="petvoyageuk_review_git",
            description=(
                "You are the code REVIEWER for the PetVoyageUK project "
                "(github.com/alexmateita-gif/petvoyageuk), alongside two "
                "other AI agents (claude-sonnet, minimax) who actually write "
                "the code and deploy it. Your access here is READ-ONLY: "
                "only read git commands are accepted (log/show/diff/status/"
                "branch/grep/blame/ls-files/...); anything else returns "
                "'REFUZAT: <reason>'. Use this to inspect recent commits "
                "or diffs (e.g. before/after a deploy) and report findings "
                "via agent_coord_send. Never claim to have fixed or "
                "deployed anything yourself in this project - you can only "
                "observe and advise; write access and the final decision "
                "stay with claude-sonnet, minimax, and Laris. One command per "
                "call - '&&', ';', '|', '$' and quotes are rejected, so "
                "don't chain commands or add an echo/label; make separate "
                "calls instead."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": (
                            "A single read-only git command, no shell "
                            "operators - e.g. 'git log --oneline -10', "
                            "'git show <hash>', 'git diff <a> <b> --stat', "
                            "'git status'. One command per call."
                        ),
                    }
                },
                "required": ["command"],
            },
            handler=review_git,
        ),
    ]
