"""Conversational file-operation handler — routes direct file requests through tool_caller.

REGISTRY INJECTION (PHASE 4):
=============================
file_op_result() now requires `registry` parameter.
Uses registry.settings.agents.get() for model access.

ARCHITECTURE (routing + TYPE_CHECKING + Registry):
==================================================
This module is the *only* file in tools/ that legitimately touches
supervisor/ at all, because it is the conversational bridge into the
orchestrator. To break the tools -> supervisor -> tools cycle, all
supervisor.* imports (and the tools.FILE_TOOLS re-export) are performed
lazily inside ``file_op_result()``. Only ``tools.tool_runner`` is
imported at module level — it is a leaf of the tools/ subtree.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Final

from tools.tool_runner import _call_with_tools

if TYPE_CHECKING:
    from config.registry import ServiceRegistry
    from supervisor.types import Plan, SupervisorResult

log = logging.getLogger("goat2.tools.file.op_response")

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
    registry: "ServiceRegistry",
    style: str = "",
) -> "SupervisorResult":
    """
    Run tool_caller for a conversational file-operation request.

    REGISTRY INJECTION (PHASE 4):
    =============================
    Requires registry parameter. Uses registry.settings.agents.get() for model access.

    Args:
        intent:    User intent (used only as SupervisorResult.intent).
        messages:  Conversation messages to send to the model.
        profile:   User profile string (system context).
        summary:   Previous-session summary string (system context).
        mem_ctx:   Memory context string (system context).
        t0:        Monotonic start time (used to compute total_duration_s).
        registry:  The application ServiceRegistry (DI container).
        style:     Optional style/mirror instruction.

    Returns:
        SupervisorResult wrapping the tool_caller reply.
    """
    # Lazy imports — break tools -> supervisor -> tools cycle.
    from tools import FILE_TOOLS
    from supervisor.types import Plan, SupervisorResult
    from supervisor.behavior.behavior_mirror import mirror_instruction

    log.debug(
        "file_op_result: intent=%r messages=%d profile=%dB summary=%dB mem_ctx=%dB style=%dB",
        intent[:60], len(messages), len(profile), len(summary), len(mem_ctx), len(style),
    )

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
