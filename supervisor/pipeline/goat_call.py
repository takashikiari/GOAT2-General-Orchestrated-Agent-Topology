"""The single GOAT LLM call — one prompt, one LLM, decides AND responds.

Middleware only assembles context; this module is the ONLY place an LLM
is invoked for the GOAT turn.

ACTION DETECTION (post-call):
  - ``start_dag`` was called → ``"dag"``
  - response ends with ``[CLARIFY]`` → ``"clarify"`` (marker stripped)
  - response is short, ends with ``?`` → ``"clarify"`` (fallback)
  - otherwise → ``"direct"``

ANTIREPEAT (post-call):
  - When the visible response overlaps the last few assistant turns
    above the configured threshold, the supervisor refuses to forward
    the same text and returns a short clarify request asking the user
    to rephrase. The default behaviour (tag-only) was a no-op and led
    to visible repetition loops. See ``supervisor.mechanisms.antirepeat``.

USAGE:
    from supervisor.pipeline.goat_call import goat_turn
    result = await goat_turn(registry=..., intent=..., goat_context=...,
                             clarity_context=..., hints=..., history_messages=...,
                             mem_ctx=..., supervisor=...)

STRICT RULES:
  - Exactly ONE ``_call_with_tools()`` call per turn.
  - Temperature from ``registry.settings.supervisor.temperature`` (no hardcoded value).
  - DSML stripping via ``utils.dsml.strip_dsml`` (single canonical impl).
  - History dedup + active anti-repetition via ``mechanisms.antirepeat``.
  - Pure-prompt helpers (system/user prompt + diagnostics) live in
    ``pipeline.prompt_helpers``; this module only orchestrates the
    call, the antirepeat gate, and the tool-result fallback.
"""
from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING, Final

from tools.tool_runner import _call_with_tools
from utils.dsml import strip_dsml
from supervisor.pipeline.prompt_helpers import (
    build_system_prompt,
    build_user_prompt,
    normalise_empty_response_with_tools,
    tool_schema_failure_hint,
)

if TYPE_CHECKING:
    from config.registry import ServiceRegistry
    from supervisor.pipeline.goat_enrichment import GoatContext

log = logging.getLogger("goat2.supervisor.pipeline.goat_call")

__all__ = ["GoatTurnResult", "goat_turn"]

# Stable token for action classification — re-imported from prompt_helpers
# only for back-compat with callers that import it from this module.
_START_DAG_TOOL: Final[str] = "start_dag"


@dataclasses.dataclass
class GoatTurnResult:
    """Result of the one GOAT LLM call.

    Attributes:
        action: ``direct`` | ``clarify`` | ``dag``
        response: User-facing text (markers stripped)
        clarification: Clarifying question (when action=clarify)
        dag_session_id: Captured from start_dag (when action=dag)
        dag_instructions: Task description passed to start_dag
        source: Provenance tag; ``"repetitive"`` when the antirepeat
            gate refused to forward the LLM's response.
        called_tools: Every tool invoked, in order.
    """

    action:           str
    response:         str
    clarification:    str         = ""
    dag_session_id:  str | None  = None
    dag_instructions: str         = ""
    source:           str         = "generated"
    called_tools:     tuple[str, ...] = ()


def _classify_response(text: str, called: tuple[str, ...]) -> tuple[str, str]:
    """Map (response_text, called_tools) → (action, visible_text)."""
    if _START_DAG_TOOL in called:
        return "dag", text.strip()
    from supervisor.pipeline.prompt_helpers import _CLARIFY_MARKER
    stripped = text.rstrip()
    if stripped.lower().endswith(_CLARIFY_MARKER.lower()):
        return "clarify", stripped[: -len(_CLARIFY_MARKER)].rstrip()
    if not called and len(stripped) <= 100 and stripped.endswith("?"):
        return "clarify", stripped
    return "direct", stripped


def _collect_goat_tools(registry, supervisor, goat_session_id: str) -> list:
    """Build the GOAT tool surface — memory tools, web search, DAG tools, etc."""
    from tools import WEB_SEARCH
    from tools.dag import make_dag_tools
    from tools.system import READ_LOGS
    dag_tools = make_dag_tools(
        registry.memory_manager, goat_session_id=goat_session_id, supervisor=supervisor,
    )
    return (
        list(registry.memory_tools)
        + [WEB_SEARCH, READ_LOGS]
        + dag_tools
        + list(registry.goat_skills_tools)
        + list(getattr(registry, "dynamic_tools", []) or [])
    )


async def goat_turn(
    registry: "ServiceRegistry",
    intent: str,
    goat_context: "GoatContext",
    clarity_context,  # ClarityContext (or None)
    hints: list[str],
    history_messages: list[dict[str, str]],
    mem_ctx: str = "",
    *,
    style: str = "",
    turn: int = 1,
    goat_session_id: str = "",
    supervisor=None,
) -> GoatTurnResult:
    """The one GOAT LLM call. Decides and responds in one pass.

    Pipeline:
      1. Assemble system + user prompt (no LLM).
      2. Deduplicate history (drop near-duplicate assistant messages).
      3. Make the single LLM call.
      4. Strip DSML + apply the tool-result fallback when LLM is silent.
      5. Classify action + apply the **active** antirepeat gate — if
         the response echoes recent turns above threshold, refuse to
         forward and return a short clarify asking the user to
         rephrase. Tag ``source = "repetitive"``.

    Args:
        registry: ServiceRegistry (settings, tools, memory).
        intent: Raw user intent.
        goat_context: Pre-built ``GoatContext`` (no LLM).
        clarity_context: Pre-built ``ClarityContext`` (no LLM), or None.
        hints: Soft hints from past corrections.
        history_messages: Prior ``{role, content}`` list.
        mem_ctx: Pre-rendered memory-context block.
        style: Raw style profile text (from Letta).
        turn: 1-based turn number (for logging).
        goat_session_id: GOAT session id (for DAG tool routing).
        supervisor: The live GoatSupervisor (for tool wiring).

    Returns:
        GoatTurnResult with action, response, and (when applicable)
        repetition-flagged source. Always non-None; the kernel must
        always respond.

    Failure mode:
        LLM exception → clarify fallback, never raise. Repetitive
        response → clarify fallback that asks the user to rephrase.
    """
    spec = registry.settings.supervisor.model
    tools = _collect_goat_tools(registry, supervisor, goat_session_id)
    user_prompt = build_user_prompt(intent, goat_context, clarity_context, hints, mem_ctx)
    sys_content = build_system_prompt(style)
    messages = [
        {"role": "system", "content": sys_content},
        {"role": "user",   "content": user_prompt},
    ]

    # Append prior conversation (deduped). The current user turn is
    # already in user_prompt, so we exclude only that one message
    # from the history slice. The most recent assistant message
    # stays in the slice — the dedup pass needs it to detect a
    # tight loop on the last reply. (BUG-008: the previous
    # implementation sliced [:-2], which dropped the prior
    # assistant message and weakened the loop-detection gate.)
    from supervisor.mechanisms.antirepeat import dedup_history
    cleaned = dedup_history(list(history_messages or [])[:-1])
    for m in cleaned:
        if isinstance(m, dict) and m.get("role") in ("user", "assistant"):
            messages.append({"role": m["role"], "content": m.get("content", "")})

    try:
        tagged = await _call_with_tools(
            spec, messages, tools,
            temperature=registry.settings.supervisor.temperature,
            tool_choice="auto",
            memory_manager=registry.memory_manager,
        )
    except Exception as exc:
        # The OpenAI-compatible SDK raises 400 on tool-call schema
        # failures (string for integer, etc.). That used to be silently
        # swallowed into the generic clarify fallback. We now log a
        # one-line diagnostic so MCP diagnose_turn can pinpoint which
        # tool/param the configured model got wrong.
        log.warning("goat_turn: LLM call failed: %s", exc)
        hint = tool_schema_failure_hint(exc)
        if hint:
            log.warning("goat_turn: tool schema failure hint: %s", hint)
            clarification = "Could you rephrase or narrow your request? (Last attempt failed on a tool-call schema mismatch.)"
        else:
            clarification = "Could you provide more details about what you'd like me to do?"
        return GoatTurnResult(action="clarify", response="", clarification=clarification)

    raw_content = strip_dsml(tagged.content or "")
    tool_results = getattr(tagged, "tool_results", None) or ()
    called_tools = tuple(tagged.called_tools or ())
    # BUG-006: when the LLM is silent but tools ran, surface a preview
    # (or an honest "no result" message) instead of the cryptic
    # "Am executat: …" placeholder.
    raw_content = normalise_empty_response_with_tools(
        raw_content, called_tools, tool_results,
    )
    action, visible = _classify_response(raw_content, called_tools)
    if action not in ("direct", "clarify", "dag"):
        action = "direct"

    # BUG-005 + BUG-009: ACTIVE antirepeat. If the visible response
    # echoes a recent assistant turn above threshold, refuse to forward
    # the same text and return a short clarify asking the user to
    # rephrase. Tagged ``source = "repetitive"`` so downstream channels
    # can deprioritize it further if needed.
    from supervisor.mechanisms.antirepeat import is_repetitive, repetitive_response_text
    repetitive = is_repetitive(visible, list(history_messages or []))
    if repetitive:
        log.warning(
            "goat_turn: response flagged repetitive (action=%s response_len=%d) — refusing to repeat",
            action, len(visible),
        )
        return GoatTurnResult(
            action="clarify",
            response="",
            clarification=repetitive_response_text(),
            source="repetitive",
            called_tools=called_tools,
        )

    log.info(
        "goat_turn: action=%s called=%s response_len=%d",
        action, list(called_tools), len(visible),
    )
    return GoatTurnResult(
        action=action,
        response=visible,
        clarification=visible if action == "clarify" else "",
        dag_session_id=None,
        dag_instructions="",
        source=tagged.source,
        called_tools=called_tools,
    )