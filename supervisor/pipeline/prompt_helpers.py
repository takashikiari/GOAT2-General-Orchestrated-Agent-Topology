"""Pure prompt + diagnostics helpers for the GOAT one-call pipeline.

Extracted from ``goat_call.py`` to keep that file under the 260-line
ceiling. Everything here is **pure**: no I/O, no LLM, no regex
(``str.find`` / ``str.split`` only — substring matching against
fixed string patterns keeps the surface provider-agnostic and easy
to reason about).

USAGE:
    from supervisor.pipeline.prompt_helpers import (
        _CLARIFY_MARKER, _MAX_INTENT_CHARS,
        build_user_prompt, build_system_prompt, tool_schema_failure_hint,
        normalise_empty_response_with_tools,
    )

WHY NO REGEX:
    The original implementation used ``re.compile`` for the tool-failure
    signature. Switching to substring matching removes a stdlib import
    and keeps the helpers safe to call from any environment (including
    contexts that pin ``re`` away for sandboxing). The patterns we
    match are literal — ``<function=NAME>{ARGS}`` — so substring /
    split is functionally equivalent and easier to audit.
"""
from __future__ import annotations

import logging
from typing import Any, Final

log = logging.getLogger("goat2.supervisor.pipeline.prompt_helpers")

__all__ = [
    "_CLARIFY_MARKER",
    "_MAX_INTENT_CHARS",
    "build_user_prompt",
    "build_system_prompt",
    "tool_schema_failure_hint",
    "normalise_empty_response_with_tools",
]


# Stable tokens mirrored from goat_call.py so action classification and
# LLM marker stripping agree on the same constants.
_CLARIFY_MARKER:   Final[str] = "[CLARIFY]"
_MAX_INTENT_CHARS: Final[int] = 4_000

# Substring markers the OpenAI-compatible SDK embeds in tool-failure
# error bodies. ``<function=NAME>{ARGS}`` is the universal signature;
# type-mismatch hints use the keywords below. We match with substring
# / split — no regex — to keep this module dependency-light.
_TOOL_FAIL_OPEN:      Final[str] = "<function="
_TOOL_FAIL_MID:       Final[str] = ">{"
_TOOL_FAIL_CLOSE:     Final[str] = "}"
_TYPE_KEYWORDS:       Final[tuple[str, ...]] = ("expected integer", "expected number", "expected boolean")
_TYPE_FIELD_OPEN:     Final[str] = '"'
_TYPE_FIELD_SEP:      Final[str] = '":'
_TYPE_FIELD_VAL_END:  Final[str] = '"'


def tool_schema_failure_hint(exc: BaseException) -> str | None:
    """Best-effort ``tool.param`` hint from a tool-call schema error.

    The OpenAI-compatible SDK reports tool-call failures with a
    ``<function=NAME>{...}`` snippet in the exception body. We match
    on that signature so the supervisor can log which tool + param
    the configured model got wrong (most often: model emitted a
    string where the schema declared an integer). Provider-agnostic
    — works regardless of which model is configured in ``goat.toml``.
    Never raises.

    Args:
        exc: The exception raised by the underlying LLM SDK call.

    Returns:
        ``"tool.param got string 'value'"`` when the body contains
        the schema-failure signature AND a recognised type keyword.
        ``"tool (args: …)"`` when only the signature matches.
        ``None`` when the body is not a recognised tool-call failure.
    """
    msg = str(exc)
    open_idx = msg.find(_TOOL_FAIL_OPEN)
    if open_idx < 0:
        return None
    # Extract `<function=NAME>{ARGS}` substrings without regex.
    func_start = open_idx + len(_TOOL_FAIL_OPEN)
    mid_idx = msg.find(_TOOL_FAIL_MID, func_start)
    if mid_idx < 0:
        return None
    name_end = mid_idx
    args_start = mid_idx + len(_TOOL_FAIL_MID)
    # Find the matching `}` for the `{` we just located. We do this by
    # counting nested braces so a stray `}` inside the args body (e.g. in
    # a JSON string value) does not close the function body prematurely.
    depth = 1
    i = args_start
    while i < len(msg):
        ch = msg[i]
        if ch == _TOOL_FAIL_OPEN:  # '{'
            depth += 1
        elif ch == _TOOL_FAIL_CLOSE:  # '}'
            depth -= 1
            if depth == 0:
                break
        i += 1
    if depth != 0:
        return None
    close_idx = i
    tool_name = msg[func_start:name_end].strip()
    raw_args  = msg[args_start:close_idx]

    # Optional: extract a "<param>: <value>" pair from the args body
    # so we can highlight which parameter the model got wrong.
    # The args body looks like: ``{"limit": "50", "tier": "working"}``.
    # We scan for the literal sequence ``"<key>":<ws>"<value>"``.
    if any(kw in msg for kw in _TYPE_KEYWORDS):
        # Walk through every position looking for: "<key>": "  <value>"
        scan = 0
        while True:
            key_open = raw_args.find(_TYPE_FIELD_OPEN, scan)
            if key_open < 0:
                break
            key_close = raw_args.find(_TYPE_FIELD_OPEN, key_open + 1)
            if key_close <= key_open:
                break
            # The ``":`` we want starts at index ``key_close``
            # (the key's closing quote). If found, the colon is at
            # ``key_close + 1``.
            sep_close = raw_args.find(_TYPE_FIELD_SEP, key_close)
            if sep_close != key_close:
                scan = key_close + 1
                continue
            # Now consume optional whitespace then expect an opening quote.
            after_sep = sep_close + len(_TYPE_FIELD_SEP)
            ws_end = after_sep
            while ws_end < len(raw_args) and raw_args[ws_end] in (" ", "\t"):
                ws_end += 1
            if ws_end >= len(raw_args) or raw_args[ws_end] != _TYPE_FIELD_OPEN:
                scan = key_close + 1
                continue
            val_start = ws_end + 1
            val_end = raw_args.find(_TYPE_FIELD_VAL_END, val_start)
            if val_end <= val_start:
                scan = key_close + 1
                continue
            return (
                f"{tool_name}.{raw_args[key_open + 1:key_close]} "
                f"got string {raw_args[val_start:val_end]!r}"
            )
    return f"{tool_name} (args: {raw_args[:80]})"


def build_user_prompt(
    intent: str,
    goat_ctx: Any,
    clarity_ctx: Any,
    hints: list[str],
    mem_ctx: str,
) -> str:
    """Compose the user message — pure, no LLM.

    Args:
        intent: Raw user intent (truncated to ``_MAX_INTENT_CHARS``).
        goat_ctx: Pre-built ``GoatContext`` (must expose ``to_prompt()``).
        clarity_ctx: Pre-built ``ClarityContext`` (or None).
        hints: Soft hints from past user corrections.
        mem_ctx: Pre-rendered memory-context block (or empty string).

    Returns:
        A single string ready to send as the ``user`` message.
    """
    parts: list[str] = [f"User message: {intent[:_MAX_INTENT_CHARS]}", ""]
    # Memory block is placed EARLY in the prompt — immediately
    # after the user message — so GOAT sees what it already
    # knows in working memory BEFORE deciding to call tools.
    # Observed failure mode (session 10:57:31, 2026-06-20): GOAT
    # had the answer to "what did we talk about at 9:44" in
    # its own working memory but launched 17 tool calls to
    # search externally anyway. Putting memory first makes the
    # answer unmissable.
    if mem_ctx:
        parts.append("Read this BEFORE calling any tools — you may "
                      "already have the answer locally.")
        parts.append(mem_ctx)
        parts.append("")
    parts.append(goat_ctx.to_prompt())
    if clarity_ctx and getattr(clarity_ctx, "to_prompt", None):
        parts.append(clarity_ctx.to_prompt())
    if hints:
        parts.append(
            "Past user corrections (soft hints):\n"
            + "\n".join(f"- {h}" for h in hints)
        )
    parts.extend([
        "",
        ("If you need a DAG (multi-step research / code / analysis), "
         "call the start_dag tool with a self-contained task description. "
         f"If you need a clarifying question, end your reply with {_CLARIFY_MARKER}. "
         "Otherwise answer."),
    ])
    return "\n".join(parts)


def build_system_prompt(style: str) -> str:
    """System message = GOAT identity + (optional) style mirror.

    Identity import is lazy to avoid a cycle through ``supervisor/__init__``.

    Args:
        style: Raw style profile text from Letta; empty string omits
            the style directive.

    Returns:
        The composed system prompt string.
    """
    from supervisor.identity import GOAT_SYSTEM, _build_style_directive
    parts = [GOAT_SYSTEM]
    if style:
        directive = _build_style_directive(style)
        if directive:
            parts.append(directive)
    return "\n".join(parts)


def normalise_empty_response_with_tools(
    raw_content: str,
    called_tools: tuple[str, ...],
    tool_results: tuple[str, ...] | list[str] | None,
    *,
    max_preview_chars: int = 500,
) -> str:
    """When the LLM is silent after tool calls, surface something useful.

    Three branches:
      1. silent content + tools called + tool results available →
         return ``"[Result for TOOL]\\nPREVIEW"`` (BUG-006 fix).
      2. silent content + tools called + no results → return a
         transparent ``"I called X but have no result to show."``
         message instead of the cryptic ``"Am executat: …"`` placeholder.
      3. otherwise → return ``raw_content`` unchanged.

    Args:
        raw_content: The LLM's visible content (may be empty/whitespace).
        called_tools: Tool names invoked this turn.
        tool_results: Per-tool result strings, in the same order as
            ``called_tools``. May be empty or ``None``.
        max_preview_chars: Cap on the preview length for branch 1.

    Returns:
        A non-empty fallback string when the LLM was silent, else
        ``raw_content`` unchanged.
    """
    if raw_content.strip() or not called_tools:
        return raw_content
    results_list = list(tool_results or [])
    if results_list:
        preview = (results_list[0] or "")[:max_preview_chars]
        return f"[Result for {called_tools[0]}]\n{preview}"
    return f"I called {', '.join(called_tools)} but have no result to show."