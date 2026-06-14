"""DagPrompt formatter — pure formatting of GOAT's dag_instructions (NO LLM).

In the single-call architecture GOAT has already decided to run a DAG and emitted
``dag_instructions`` (a self-contained objective). This module just *formats* that
string into the structured ``DagPrompt`` the planner consumes — no LLM call, no
agent/criteria inference. The planner (a specialized DAG LLM) decomposes the
objective and selects agents; leaving ``required_agents`` empty hands it that job.

Only fixed strings here are JSON/struct field names. No regex, no hardcoded rules.
"""
from __future__ import annotations

import dataclasses
import logging
import uuid

log = logging.getLogger("goat2.supervisor.pipeline.dag_prompt_builder")

__all__ = ["DagPrompt", "build_dag_prompt"]


@dataclasses.dataclass
class DagPrompt:
    """Structured execution instructions GOAT passes to the DAG planner.

    Attributes:
        task_id: UUID4 bound to this build call for traceability.
        technical_prompt: Self-contained objective for the planner (GOAT's
            ``dag_instructions``).
        required_agents: Roles to force; empty lets the planner choose.
        verification_criteria: Observable success outcomes; empty lets the
            planner/critic derive them.
        memory_updates: Whether agents should write findings to working memory.
        constraints: Task-specific limits the planner should respect.
    """

    task_id: str
    technical_prompt: str
    required_agents: list[str]
    verification_criteria: list[str]
    memory_updates: bool
    constraints: dict


def build_dag_prompt(dag_instructions: str, constraints: dict | None = None) -> DagPrompt:
    """Format GOAT's ``dag_instructions`` into a DagPrompt — pure, no LLM.

    Args:
        dag_instructions: Self-contained planner objective from GoatDecision.
        constraints: Optional task-specific limits to carry to the planner.

    Returns:
        A DagPrompt wrapping the instructions; ``required_agents`` and
        ``verification_criteria`` are left empty for the planner to decide.
    """
    prompt = DagPrompt(
        task_id=uuid.uuid4().hex,
        technical_prompt=dag_instructions or "",
        required_agents=[],
        verification_criteria=[],
        memory_updates=True,
        constraints=dict(constraints or {}),
    )
    log.debug("build_dag_prompt: task_id=%s technical_prompt=%.160s",
              prompt.task_id, prompt.technical_prompt)
    return prompt
