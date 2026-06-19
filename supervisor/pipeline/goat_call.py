"""The single GOAT LLM call — one prompt, one LLM, decides AND responds.

Middleware only assembles context; this module is the ONLY place an LLM
is invoked for the GOAT turn.

ACTION DETECTION (post-call):
  - ``start_dag`` was called → ``"dag"``
  - response ends with ``[CLARIFY]`` → ``"clarify"`` (marker stripped)
  - response is short, ends with ``?`` → ``"clarify"`` (fallback)
  - otherwise → ``"direct"``

USAGE:
    from supervisor.pipeline.goat_call import goat_turn
    result = await goat_turn(registry=..., intent=..., goat_context=...,
                             clarity_context=..., hints=..., history_messages=...,
                             mem_ctx=..., supervisor=...)

STRICT RULES:
  - Exactly ONE ``_call_with_tools()`` call per turn.
  - Temperature from ``registry.settings.supervisor.temperature`` (no hardcoded value).
  - DSML stripping via ``utils.dsml.strip_dsml`` (single canonical impl).
  - History dedup + anti-repetition tag via ``mechanisms.antirepeat``.
"""
from __future__ import annotations

import dataclasses
import logging
import re
from typing import TYPE_CHECKING, Final

from tools.tool_runner import _call_with_tools
from utils.dsml import strip_dsml

if TYPE_CHECKING:
    from config.registry import ServiceRegistry
    from supervisor.pipeline.goat_enrichment import GoatContext

log = logging.getLogger("goat2.supervisor.pipeline.goat_call")

__all__ = ["GoatTurnResult", "goat_turn"]

# Stable tokens for action classification + LLM response marker.
_CLARIFY_MARKER:   Final[str] = "[CLARIFY]"
_START_DAG_TOOL:   Final[str] = "start_dag"
_CLARIFY_MAX_CHARS: Final[int] = 100
_MAX_INTENT_CHARS: Final[int] = 4_000


@dataclasses.dataclass
class GoatTurnResult:
    """Result of the one GOAT LLM call.

    Attributes:
        action: ``direct`` | ``clarify`` | ``dag``
        response: User-facing text (markers stripped)
        clarification: Clarifying question (when action=clarify)
        dag_session_id: Captured from start_dag (when action=dag)
        dag_instructions: Task description passed to start_dag
        source: Provenance tag; ``"repetitive"`` when anti-repetition flagged it.
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
    stripped = text.rstrip()
    if stripped.lower().endswith(_CLARIFY_MARKER.lower()):
        return "clarify", stripped[: -len(_CLARIFY_MARKER)].rstrip()
    if not called and len(stripped) <= _CLARIFY_MAX_CHARS and stripped.endswith("?"):
        return "clarify", stripped
    return "direct", stripped
_TOOL_FAIL_SIGNATURE = re.compile(r"<function=([\w_]+)>\{([^}]*)\}")
_TYPE_MISMATCH_HINT = re.compile(r'"(\w+)":\s*"([^"]*)"')
_TYPE_KEYWORDS = ("expected integer", "expected number", "expected boolean")


def _tool_schema_failure_hint(exc: BaseException) -> str | None:
    """Best-effort ``tool.param`` hint from a tool-call schema error.

    The OpenAI-compatible SDK (universal transport for all configured
    providers) reports tool-call failures with a ``<function=NAME>{...}``
    snippet in the exception body. We match on that signature so the
    supervisor can log which tool + param the configured model got wrong
    (most often: model emitted a string where the schema declared an
    integer). Provider-agnostic — works regardless of which model is
    configured in ``goat.toml``. Never raises.
    """
    msg = str(exc)
    match = _TOOL_FAIL_SIGNATURE.search(msg)
    if not match:
        return None
    tool_name, raw_args = match.group(1), match.group(2)
    bad = _TYPE_MISMATCH_HINT.search(raw_args)
    if bad and any(kw in msg for kw in _TYPE_KEYWORDS):
        return f"{tool_name}.{bad.group(1)} got string {bad.group(2)!r}"
    return f"{tool_name} (args: {raw_args[:80]})"


def _build_user_prompt(intent, goat_ctx, clarity_ctx, hints, mem_ctx) -> str:
    """Compose the user message — pure, no LLM."""
    parts = [f"User message: {intent[:_MAX_INTENT_CHARS]}", ""]
    parts.append(goat_ctx.to_prompt())
    if clarity_ctx and getattr(clarity_ctx, "to_prompt", None):
        parts.append(clarity_ctx.to_prompt())
    if hints:
        parts.append("Past user corrections (soft hints):\n" + "\n".join(f"- {h}" for h in hints))
    if mem_ctx:
        parts.extend(["", mem_ctx])
    parts.extend([
        "",
        ("If you need a DAG (multi-step research / code / analysis), "
         "call the start_dag tool with a self-contained task description. "
         f"If you need a clarifying question, end your reply with {_CLARIFY_MARKER}. "
         "Otherwise answer."),
    ])
    return "\n".join(parts)


def _build_system_prompt(style: str) -> str:
    """System message = GOAT identity + (optional) style mirror.

    Identity import is lazy to avoid a cycle through ``supervisor/__init__``.
    """
    from supervisor.identity import GOAT_SYSTEM, _build_style_directive
    parts = [GOAT_SYSTEM]
    if style:
        directive = _build_style_directive(style)
        if directive:
            parts.append(directive)
    return "\n".join(parts)


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
        GoatTurnResult with action, response, and (when applicable) repetition-flagged source.

    Failure mode:
        LLM exception → clarify fallback, never raise. The kernel must always respond.
    """
    spec = registry.settings.supervisor.model
    tools = _collect_goat_tools(registry, supervisor, goat_session_id)
    user_prompt = _build_user_prompt(intent, goat_context, clarity_context, hints, mem_ctx)
    sys_content = _build_system_prompt(style)
    messages = [
        {"role": "system", "content": sys_content},
        {"role": "user",   "content": user_prompt},
    ]

    # Append prior conversation (deduped). The current user turn is
    # already in user_prompt; the last 2 messages are skipped to
    # avoid double-feeding them.
    from supervisor.mechanisms.antirepeat import dedup_history
    cleaned = dedup_history(list(history_messages or [])[:-2])
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
        hint = _tool_schema_failure_hint(exc)
        if hint:
            log.warning("goat_turn: tool schema failure hint: %s", hint)
            clarification = "Could you rephrase or narrow your request? (Last attempt failed on a tool-call schema mismatch.)"
        else:
            clarification = "Could you provide more details about what you'd like me to do?"
        return GoatTurnResult(action="clarify", response="", clarification=clarification)

    raw_content = strip_dsml(tagged.content or "")
    if not raw_content.strip() and tagged.called_tools:
        raw_content = f"Am executat: {', '.join(tagged.called_tools)}"
    action, visible = _classify_response(raw_content, tagged.called_tools)
    if action not in ("direct", "clarify", "dag"):
        action = "direct"

    # Anti-repetition: we do NOT regenerate (would add an LLM call). The
    # signal is exposed via ``source`` so downstream channels can deprioritize.
    from supervisor.mechanisms.antirepeat import is_repetitive
    repetitive = is_repetitive(visible, list(history_messages or []))
    final_source = "repetitive" if repetitive else tagged.source
    if repetitive:
        log.warning("goat_turn: response flagged repetitive (action=%s response_len=%d)", action, len(visible))
    else:
        log.info("goat_turn: action=%s called=%s response_len=%d", action, list(tagged.called_tools), len(visible))
    return GoatTurnResult(
        action=action,
        response=visible,
        clarification=visible if action == "clarify" else "",
        dag_session_id=None,
        dag_instructions="",
        source=final_source,
        called_tools=tuple(tagged.called_tools),
    )