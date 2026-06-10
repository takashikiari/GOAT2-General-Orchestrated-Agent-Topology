"""Conversational file-operation handler — routes direct file requests through tool_caller.

REGISTRY INJECTION (PHASE 4):
=============================
file_op_result() now requires `registry` parameter.
Uses registry.settings.agents.get() for model access.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Final

from tools.tool_runner import _call_with_tools
from supervisor.types import Plan, SupervisorResult

if TYPE_CHECKING:
    from config.registry import Registry

__all__ = ["file_op_result"]

_SYSTEM: Final[str] = (
    "You are GOAT — a personal assistant. "
    "File tools available: file_read(path), file_write(path, content), "
    "file_create(path, content), file_list(path). "
    "Paths may be absolute (e.g. ~/Desktop/notes.txt) or relative to workspace. "
    "If a tool returns ERROR or is unavailable, say 'tool not connected' — never hallucinate. "
    "Execute file operations directly without asking the user to do them manually."
)


async def file_op_result(
    intent: str,
    messages: list[dict[str, str]],
    profile: str,
    summary: str,
    mem_ctx: str,
    t0: float,
    registry: "Registry",
    style: str = "",
) -> SupervisorResult:
    """
    Run tool_caller for a conversational file-operation request.

    REGISTRY INJECTION (PHASE 4):
    =============================
    Requires registry parameter. Uses registry.settings.agents.get() for model access.
    """
    from tools import FILE_TOOLS
    from supervisor.behavior_mirror import mirror_instruction

    parts = [_SYSTEM]
    if style:
        directive = mirror_instruction(style)
        if directive:
            parts.append(f"\n{directive}")
    if profile:
        parts.append(f"\nUser profile:\n{profile}")
    if summary:
        parts.append(f"\nPrevious sessions:\n{summary}")
    sys_msg = {"role": "system", "content": "".join(parts)}
    ctx_msgs = [{"role": "system", "content": mem_ctx}] if mem_ctx else []
    msgs = [sys_msg, *ctx_msgs, *messages]
    reply = await _call_with_tools(
        registry.settings.agents.get("tool_caller"), msgs, FILE_TOOLS, temperature=0.7,
    )
    return SupervisorResult(
        intent=intent, plan=Plan(tasks=[]), results={},
        critique="", summary=reply,
        total_duration_s=time.monotonic() - t0,
    )
