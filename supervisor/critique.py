"""Critique and synthesis agents — review task results and produce final answer."""
from __future__ import annotations

from config.settings import settings
from supervisor.types import AgentResult
from supervisor.llm_utils import _call_llm, _format_dep_context
from supervisor.identity import _system_with_profile

__all__ = ["critique_results", "synthesize_results"]


async def critique_results(intent: str, results: dict[str, AgentResult], lang: str = "") -> str:
    """Critic agent reviews the full set of completed task results end-to-end."""
    context  = _format_dep_context(results)
    lang_pfx = f"Respond in {lang}. " if lang and lang.lower() != "english" else ""
    return await _call_llm(
        settings.agents.get("critic"),
        [
            {"role": "system", "content": (
                f"{lang_pfx}You are a critical reviewer for a multi-agent pipeline. "
                "Evaluate all agent outputs for correctness, completeness, and alignment "
                "with the original intent. One concise assessment paragraph, then a "
                "bullet list of issues and suggestions."
            )},
            {"role": "user", "content": (
                f"Original intent: {intent}\n\n{context}\n\nProvide a critical review."
            )},
        ],
    )


async def synthesize_results(
    intent: str,
    results: dict[str, AgentResult],
    critique: str,
    profile: str = "",
    style: str = "",
    lang: str = "",
    session_summary: str = "",
) -> str:
    """Synthesize agent outputs into a terse, persona-matched final answer."""
    context  = _format_dep_context(results)
    sys_base = _system_with_profile(profile, session_summary, style)
    lang_sfx = f"\nRespond in {lang}." if lang and lang.lower() != "english" else ""
    return await _call_llm(
        settings.agents.get("planner"),
        [
            {"role": "system", "content": (
                f"{sys_base}{lang_sfx}\n\nDeliver a direct answer that mirrors the user's tone. "
                "State only facts present in the agent outputs above — do not infer or approximate. "
                "If information is absent or errored, state it was not found — do not generate content. "
                "No headers, no tables, no preamble labels. No apologies. No questions at the end."
            )},
            {"role": "user", "content": (
                f"Original intent: {intent}\n\n{context}\n\n"
                f"Critique notes: {critique}"
            )},
        ],
        temperature=0.7,
    )
