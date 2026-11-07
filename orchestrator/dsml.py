"""orchestrator.dsml — recognise and strip DeepSeek DSML tool-call markup in model text.

deepseek-v4-flash sometimes writes its tool-call intent as DSML markup in `content` instead of
the structured `tool_calls` field. Observed spellings:
  <｜｜DSML｜｜tool_calls> … <｜｜DSML｜｜invoke name="t"> … <｜｜DSML｜｜parameter name="k" string="true">v</…>
  <｜｜DSML｜｜ calls>     … <｜｜DSML｜｜ invoke name="t"> … (space after the bars, "calls") — 2026-11-07
The second form slipped past the old exact regexes, so neither the DSML round nor the last-resort
strip ran and raw markup reached Telegram (5 messages 2026-11-05..07). The patterns below accept
one or two bars (fullwidth ｜ or ASCII |), optional whitespace, and tool_calls/function_calls/calls.
"""
from __future__ import annotations

import re

from utils.logging.setup import get_logger

log = get_logger(__name__)
__all__ = ["DSML_BLOCK", "DSML_INVOKE", "DSML_PARAM", "strip_dsml"]

_BARS = r"[｜|]{1,2}"
_OPEN = rf"<\s*{_BARS}\s*DSML\s*{_BARS}\s*"
_CLOSE = rf"<\s*/\s*{_BARS}\s*DSML\s*{_BARS}\s*"
_CALLS = r"(?:tool_calls|function_calls|calls)"

DSML_BLOCK = re.compile(rf"{_OPEN}{_CALLS}\s*>(.*?){_CLOSE}{_CALLS}\s*>", re.DOTALL)
DSML_INVOKE = re.compile(rf'{_OPEN}invoke\s+name="([^"]+)"\s*>(.*?){_CLOSE}invoke\s*>', re.DOTALL)
DSML_PARAM = re.compile(
    rf'{_OPEN}parameter\s+name="([^"]+)"\s+string="([^"]*)"[^>]*>(.*?){_CLOSE}parameter\s*>', re.DOTALL)
# any single DSML tag left over (unclosed block, cut-off reply, unknown tag name)
_ANY_TAG = re.compile(rf"(?:{_OPEN}|{_CLOSE})[^>]*>")
# an opened block the model never closed: drop it to the end, it is an unexecuted call
_OPEN_BLOCK = re.compile(rf"{_OPEN}{_CALLS}\s*>.*\Z", re.DOTALL)


def strip_dsml(text: str, chat_id: str = "") -> str:
    """Text with every trace of DSML markup removed; logs once when something was stripped."""
    if not text or "DSML" not in text:
        return text
    out = _OPEN_BLOCK.sub("", DSML_BLOCK.sub("", text))
    out = _ANY_TAG.sub("", out).strip()
    if out != text.strip():
        log.warning("DSML markup stripped from final reply chat=%s (%d → %d chars)", chat_id, len(text), len(out))
    return out
