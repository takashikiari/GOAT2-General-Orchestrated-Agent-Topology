"""Pre-execution validation gates for the GOAT DAG pipeline.

Two LLM-backed gates that run before a DAG is spawned:

  1. ``check_intent_clarity_gate`` — is the user's intent clear enough to act on?
  2. ``validate_dag_prompt_gate`` — does GOAT's decision, once FORMATTED into a
     DagPrompt by the Prompter, contain everything the DAG needs?

Both fit the architecture contract *GOAT decides → Prompter formats → DAG
executes*: the gates never decide WHAT to do, they only check readiness and, on
failure, return a ClarityResult carrying a specific clarification question.
Extracted from GoatSupervisor so the supervisor class stays focused on routing.

Each gate takes the live ``supervisor`` for state access — no singletons. Both
default to ``clear=True`` on any internal error so ambiguity never hard-blocks
the pipeline.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from supervisor.supervisor import GoatSupervisor
    from supervisor.pipeline.intent_clarity import ClarityResult
    from supervisor.pipeline.goat_enrichment import GoatDecision

log = logging.getLogger("goat2.supervisor.pipeline.gates")

__all__ = ["check_intent_clarity_gate", "validate_dag_prompt_gate"]


async def check_intent_clarity_gate(
    supervisor: "GoatSupervisor", intent: str, mem_ctx: str
) -> "ClarityResult":
    """Return ClarityResult from the LLM intent-clarity check.

    Feeds the full recent dialogue (both user and assistant turns, ~5 turns) so the
    LLM can interpret short messages in context. Returns ClarityResult(clear=True) on
    any failure — ambiguity never hard-blocks.
    """
    from supervisor.pipeline.intent_clarity import check_intent_clarity
    from supervisor.classification.classifier_prompt import format_dialogue
    history_text = format_dialogue(supervisor._history.messages) if supervisor._history else ""
    log.debug("check_intent_clarity_gate: intent=%.80s", intent)
    return await check_intent_clarity(intent, mem_ctx, history_text, supervisor.registry)


async def validate_dag_prompt_gate(
    supervisor: "GoatSupervisor",
    decision: "GoatDecision",
    intent: str,
    mem_ctx: str,
) -> "ClarityResult":
    """Format GOAT's decision into a DagPrompt and validate it for completeness.

    The Prompter (build_dag_prompt) formats the already-made GoatDecision; this
    gate then checks completeness and specificity. Returns ClarityResult(
    clear=False) with a specific clarification_question if the prompt is missing
    required information. Defaults to clear=True on any exception so validation
    never hard-blocks the pipeline.
    """
    from supervisor.pipeline.dag_prompt_builder import build_dag_prompt, validate_dag_prompt
    from supervisor.pipeline.intent_clarity import ClarityResult
    from supervisor.classification.classifier_prompt import format_history
    history_text = format_history(supervisor._history.messages) if supervisor._history else ""
    try:
        dag_prompt = await build_dag_prompt(decision, mem_ctx, history_text, supervisor.registry)
        return await validate_dag_prompt(dag_prompt, intent, supervisor.registry)
    except Exception as e:
        log.debug("validate_dag_prompt_gate failed — defaulting to clear: %s", e)
        return ClarityResult(clear=True, missing=[], clarification_question="")
