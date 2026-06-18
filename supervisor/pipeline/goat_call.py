"""The single GOAT LLM call — one prompt, one LLM, decides AND responds.

Replaces the former two-call sequence
(``goat_decision.decide`` + ``identity.direct_response``) with
one tool-enabled LLM call. Middleware only assembles context
(no LLM); the call below is the **one** LLM invocation per turn.
DAG agents keep their own specialized LLMs downstream.

ACTION DETECTION (post-call):
    The LLM uses tools naturally. The action is inferred from
    the tool-call trace and a small convention in the response:

    - ``start_dag`` was called → action = ``dag``; the spawned
      session id is captured from the supervisor's
      ``_active_dag_tasks`` (the tool handle already wrote the
      instructions and spawned the DAG).
    - Otherwise, if the response ends with the marker
      ``[CLARIFY]`` → action = ``clarify``; the marker is stripped.
    - Otherwise, if no tools were used and the response is short
      and ends with ``?`` → action = ``clarify`` (fallback for
      when the LLM forgets the marker).
    - Otherwise → action = ``direct``.
"""
from __future__ import annotations

import dataclasses
import logging
import re
from typing import TYPE_CHECKING, Final

from tools.tool_runner import _call_with_tools
from utils.llm_utils import strip_dsml

if TYPE_CHECKING:
    from config.registry import ServiceRegistry
    from supervisor.pipeline.goat_enrichment import GoatContext
    from supervisor.pipeline.intent_clarity import ClarityContext

log = logging.getLogger("goat2.supervisor.pipeline.goat_call")

__all__ = ["GoatTurnResult", "goat_turn"]

_CLARIFY_MARKER: Final[str] = "[CLARIFY]"
_START_DAG_TOOL: Final[str] = "start_dag"
_CLARIFY_MAX_CHARS: Final[int] = 100
_TRAILING_QUESTION_RE: Final[re.Pattern[str]] = re.compile(r"\?\s*$")
_MAX_INTENT_CHARS: Final[int] = 4_000


@dataclasses.dataclass
class GoatTurnResult:
    """Result of the single GOAT LLM call.

    Attributes:
        action: ``direct`` | ``clarify`` | ``dag``.
        response: User-facing text (markers stripped).
        clarification: The clarification question when action=clarify.
        dag_session_id: Captured from start_dag tool when action=dag.
        dag_instructions: Task description passed to start_dag.
        source: Provenance tag from the tool runner.
        called_tools: Names of every tool invoked, in order.
    """

    action: str
    response: str
    clarification: str = ""
    dag_session_id: str | None = None
    dag_instructions: str = ""
    source: str = "generated"
    called_tools: tuple[str, ...] = ()


def _classify_response(text: str, called: tuple[str, ...]) -> tuple[str, str]:
    """Map (response_text, called_tools) → (action, visible_text)."""
    if _START_DAG_TOOL in called:
        return "dag", text.strip()
    stripped = text.rstrip()
    if stripped.lower().endswith(_CLARIFY_MARKER.lower()):
        return "clarify", stripped[: -len(_CLARIFY_MARKER)].rstrip()
    if (
        not called
        and len(stripped) <= _CLARIFY_MAX_CHARS
        and _TRAILING_QUESTION_RE.search(stripped) is not None
    ):
        return "clarify", stripped
    return "direct", stripped


def _build_user_prompt(
    intent: str, goat_ctx, clarity_ctx, hints, mem_ctx: str,
) -> str:
    """Compose the user message — pure, no LLM."""
    parts = [f"User message: {intent[:_MAX_INTENT_CHARS]}", ""]
    parts.append(goat_ctx.to_prompt())
    parts.append(clarity_ctx.to_prompt())
    if hints:
        parts.append("Past user corrections (soft hints):\n" + "\n".join(f"- {h}" for h in hints))
    if mem_ctx:
        parts.append("")
        parts.append(mem_ctx)
    parts.append("")
    parts.append(
        "If you need a DAG (multi-step research / code / analysis), call the "
        "start_dag tool with a self-contained task description. If you need a "
        f"clarifying question, end your reply with {_CLARIFY_MARKER}. Otherwise answer."
    )
    return "\n".join(parts)


def _build_system_prompt(
    profile: str, summary: str, style: str, turn: int, onboarding_done: bool,
) -> str:
    """System message = GOAT identity + profile + style + summary + onboarding."""
    from supervisor.identity import _system_with_profile
    from supervisor.identity_onboarding import _build_adaptive_hint, _build_welcome_message
    base = _system_with_profile(profile, summary, style)
    block = _build_welcome_message(turn, onboarding_done) or _build_adaptive_hint(turn, onboarding_done)
    return base + block if block else base


def _collect_goat_tools(registry, supervisor, goat_session_id: str) -> list:
    """Build the GOAT tool surface — same as the old direct_response."""
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
    clarity_context: "ClarityContext",
    hints: list[str],
    history_messages: list[dict[str, str]],
    mem_ctx: str = "",
    *,
    profile: str = "",
    summary: str = "",
    style: str = "",
    turn: int = 1,
    onboarding_done: bool = True,
    goat_session_id: str = "",
    supervisor=None,
) -> GoatTurnResult:
    """The single GOAT LLM call. Decides and responds in one pass.

    Degrades gracefully on LLM failure: logs WARNING and returns
    a clarification fallback rather than crashing the kernel.
    """
    spec = registry.settings.supervisor.model
    tools = _collect_goat_tools(registry, supervisor, goat_session_id)
    user_prompt = _build_user_prompt(intent, goat_context, clarity_context, hints, mem_ctx)
    sys_content = _build_system_prompt(profile, summary, style, turn, onboarding_done)
    messages = [
        {"role": "system", "content": sys_content},
        {"role": "user", "content": user_prompt},
    ]
    # Append the prior conversation so the LLM has context for
    # follow-ups. The current user turn is already in user_prompt.
    for m in (history_messages or [])[:-2]:
        if m.get("role") in ("user", "assistant"):
            messages.append({"role": m["role"], "content": m["content"]})

    try:
        tagged = await _call_with_tools(
            spec, messages, tools,
            temperature=registry.settings.supervisor.temperature, tool_choice="auto",
            memory_manager=registry.memory_manager,
        )
    except Exception as exc:
        log.warning("goat_turn: LLM call failed: %s", exc)
        return GoatTurnResult(
            action="clarify",
            response="",
            clarification="Could you provide more details about what you'd like me to do?",
        )

    raw_content = tagged.content or ""
    # Strip DeepSeek DSML markers via the single canonical implementation
    # in utils.llm_utils (used by history.py, __main__.py, and here).
    raw_content = strip_dsml(raw_content)
    if not raw_content.strip() and tagged.called_tools:
        raw_content = f"Am executat: {', '.join(tagged.called_tools)}"
    action, visible = _classify_response(raw_content, tagged.called_tools)
    if action not in ("direct", "clarify", "dag"):
        action = "direct"
    log.info(
        "goat_turn: action=%s called=%s response_len=%d",
        action, list(tagged.called_tools), len(visible),
    )
    return GoatTurnResult(
        action=action,
        response=visible,
        clarification=visible if action == "clarify" else "",
        dag_session_id=None,
        dag_instructions="",
        source=tagged.source,
        called_tools=tuple(tagged.called_tools),
    )
