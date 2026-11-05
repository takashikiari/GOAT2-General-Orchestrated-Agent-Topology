"""Run the agent-coord CLI as a subprocess (no agent-coord import); formatting lives in agent_coord_fmt."""
from __future__ import annotations

import asyncio
import contextlib
import json
import os

from tools import agent_coord_skill_config as cfg
from tools.agent_coord_fmt import NOTE, fmt_journal, fmt_sessions, one_line, parse_json

__all__ = ["run", "NOTE", "fmt_sessions", "fmt_journal", "supervisor_identity", "one_line", "parse_json"]


async def run(*args: str) -> tuple[int, str, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            cfg.CLI_PATH, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except FileNotFoundError:
        return 127, "", f"CLI agent-coord lipsă: {cfg.CLI_PATH}"
    except OSError as e:
        return 1, "", str(e)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), cfg.TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
        return 124, "", "timeout agent-coord"
    return proc.returncode, out.decode(errors="replace"), err.decode(errors="replace")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:  # PermissionError: alive, other owner
        pass
    return True


def supervisor_identity(path) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return ""
    if not isinstance(data, dict):
        return ""
    pid = data.get("pid")
    if isinstance(pid, int) and not isinstance(pid, bool) and not _alive(pid):
        return ""
    return str(data.get("identity") or "")
