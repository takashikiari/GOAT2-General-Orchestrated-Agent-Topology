"""GOAT 2.0 identity — the operational rules of the system
prompt. NO personality, NO tool enumeration, NO LLM.

The system prompt is composed of three parts:

  1. A ``Today's date: YYYY-MM-DD`` anchor (computed at import
     time) so the LLM can ground its temporal reasoning in
     the actual day. Without this anchor, the LLM has been
     observed to fabricate year-2025 timestamps when asked
     about "this morning" or "today".
  2. ``GOAT_SYSTEM`` — the 10 operational rules.
  3. The style mirror directive (``behavior.mirror``) — only
     when a style profile is loaded.

Design rationale:
  - Personality, style, and capabilities flow dynamically from
    three sources (Letta persona, behavior profile, registry
    tools). Hardcoding them in the system prompt would mean
    updating the prompt every time a tool or persona is added.
  - Keeping ``GOAT_SYSTEM`` to operational rules also makes
    A/B-testing rules painless: change one string, re-run.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Final

log = logging.getLogger("goat2.supervisor.identity")

__all__ = ["GOAT_SYSTEM", "_build_style_directive"]


# Today's date is computed at import time and prepended to the
# system prompt. This grounds the LLM's temporal reasoning —
# without it, the model has no anchor for "today" or "yesterday"
# and falls back to guessing (often an old year, observed in
# session logs).
_TODAY_ISO: Final[str] = datetime.now(timezone.utc).strftime("%Y-%m-%d")


# Operational rules only. Personality, style, and capabilities
# are injected dynamically. Each rule below is a hard constraint
# the LLM must follow on every call; everything else is data,
# not identity.
GOAT_SYSTEM: Final[str] = (
    f"Today's date: {_TODAY_ISO}\n"
    "1. Never invent facts. Use tools, working memory, or the [Memory] block. "
    "Report from it directly when present.\n"
    "2. Prefer tools and memory over assumptions. Verify before stating facts.\n"
    "3. Respond in the user's language. Adapt to the user's communication style.\n"
    "4. Use memory as context, not as a script. Never reuse previous responses verbatim.\n"
    "5. Avoid repetitive patterns. Reformulate naturally when similar situations occur.\n"
    "6. Be concise when possible, detailed when necessary. No filler, no preamble.\n"
    "7. Questions are allowed when they help task completion or natural conversation.\n"
    "8. After tool calls, always provide a visible response. Never return empty.\n"
    "9. Context entries are labeled [FRESH/RECENT/OLD][CONV/DAG/GOAT/SYS]. "
    "Prioritize [FRESH][CONV]. Treat [OLD][DAG] as potentially stale — verify before using.\n"
    "10. Prefer most recent verified information over older memory when conflicts exist.\n"
    "11. When asked what you did, ALWAYS report from the structured action log "
    "in the [Present] block (the 'Last turn actions:' section). "
    "Never invent actions from your own previous text — your previous "
    "response is NOT proof of which tools succeeded or failed."
)


def _build_style_directive(style: str) -> str:
    """Render the style-mirror directive, or ``""`` when style is empty.

    Thin wrapper over ``behavior.mirror.mirror_instruction`` that
    swallows the (very unlikely) import failure so the system
    prompt can always be assembled.

    Args:
        style: Raw ``key: value`` profile text from Letta.

    Returns:
        The single-line directive, or ``""`` when style is empty
        or unparseable.
    """
    if not style:
        return ""
    try:
        from supervisor.behavior.mirror import mirror_instruction
        return mirror_instruction(style)
    except Exception as exc:  # noqa: BLE001
        log.debug("_build_style_directive: mirror failed: %s", exc)
        return ""
