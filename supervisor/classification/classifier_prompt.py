"""Prompt builder for the pure LLM intent classifier.

The prompt is plain prose — no keywords, no regex hints, no scoring
rules. The model is told in natural language what GOAT can do, what
requires deep thinking, and is given the full context (history,
active DAGs, profile, override, prior corrections). The model
returns exactly one word: conversational, analytical, or complex.
"""
from __future__ import annotations

import logging
from typing import Final

log = logging.getLogger("goat2.supervisor.classification.classifier_prompt")

__all__ = ["build_classifier_prompt", "format_active_dags", "format_hints", "format_history"]


# ── Pure LLM system prompt ──
# This is the *only* source of truth. The model sees plain prose
# describing what GOAT can do, what requires deep thinking, and the
# full context. There is no regex, no list, no scoring.
_CLASSIFIER_SYSTEM: Final[str] = (
    "You are the routing brain for GOAT, a multi-agent assistant.\n"
    "\n"
    "GOAT can answer directly (CONVERSATIONAL) when the request is:\n"
    "  - a question it can answer from memory or a quick web search\n"
    "  - a definition, explanation, comparison, or chitchat\n"
    "  - a small lookup, status check, or trivial single-tool task\n"
    "  - any follow-up that GOAT already has the context for\n"
    "\n"
    "GOAT should run a small DAG (ANALYTICAL) when the request needs\n"
    "1–2 sub-tasks: a focused comparison, a single code change, a\n"
    "structured summary, a short multi-part answer.\n"
    "\n"
    "GOAT should run the full DAG (COMPLEX) when the request needs\n"
    "multi-step research, code across multiple files, deep analysis\n"
    "of the codebase, system configuration, architecture decisions,\n"
    "or any task that benefits from parallel sub-tasks with review.\n"
    "\n"
    "You will be given:\n"
    "  - the current conversation (user turns only)\n"
    "  - active DAG sessions, if any\n"
    "  - the user profile (semantic summary from long-term memory)\n"
    "  - any explicit user override (force CONVERSATIONAL / COMPLEX)\n"
    "  - prior corrections from the user about similar intents\n"
    "\n"
    "Use all of this context. Apply the override if one is present.\n"
    "If a DAG is already running and the user is asking about it,\n"
    "prefer CONVERSATIONAL so GOAT can report progress.\n"
    "\n"
    "Reply with exactly one word: conversational, analytical, or complex."
)


def build_classifier_prompt(
    intent: str,
    history_text: str,
    active_dags: list[dict],
    user_profile: str,
    override: str | None,
    hints: list[str],
) -> str:
    """Build the user-side prompt sent to the classification LLM."""
    return (
        f"[Conversation history]\n{history_text}\n"
        f"\n[Active DAG sessions]\n{format_active_dags(active_dags)}\n"
        f"\n[User profile]\n{user_profile}\n"
        f"\n[User override]\n{override or 'none'}\n"
        f"\n[Prior corrections]\n{format_hints(hints)}\n"
        f"\n[Latest user message]\n{intent}\n"
        f"\nReply with exactly one word: conversational, analytical, or complex."
    )


def format_history(history: list[dict[str, str]] | None) -> str:
    """Format recent USER turns only as plain prose for the LLM."""
    if not history:
        return "(no prior conversation)"
    user_turns = [m["content"] for m in history if m.get("role") == "user"]
    if not user_turns:
        return "(no prior conversation)"
    last = user_turns[-6:]
    return "\n".join(f"- {t}" for t in last)


def format_active_dags(active: list[dict] | None) -> str:
    """Format active DAG sessions as a brief plain-text summary."""
    if not active:
        return "(no active DAG sessions)"
    lines: list[str] = []
    for s in active[:5]:
        sid = s.get("session_id", "?")
        wave = s.get("wave", "?")
        total = s.get("total_waves", "?")
        status = s.get("status", "?")
        lines.append(f"- DAG {sid}: wave {wave}/{total}, status={status}")
    return "\n".join(lines)


def format_hints(hints: list[str] | None) -> str:
    """Format prior user corrections as soft semantic hints."""
    if not hints:
        return "(no prior corrections)"
    return "\n".join(f"- {h}" for h in hints[:5])
