"""Central agent role registry for GOAT 2.0.

This module defines the canonical list of agent roles used throughout
GOAT 2.0 for task decomposition, DAG validation, and agent spawning.

Roles:
    researcher: Deep web research with forced web_search tool.
    coder: Code generation with file tool access.
    critic: Critical review and assessment.
    planner: Task decomposition into DAG structure.
    summarizer: Synthesis and aggregation of upstream outputs.
    tool_caller: General-purpose tool orchestration.
    memory: Memory classification and analysis.

Execution Roles (EXECUTION_ROLES):
    Roles that MUST invoke a real tool call — generated output is unacceptable.
    These roles require tool_choice='required' to enforce tool invocation.

Synthesis Roles (SYNTHESIS_ROLES):
    Roles that generate content without tool calls.
    Output source is always "generated".

All files should import from this module instead of hardcoding role strings.
"""
from __future__ import annotations

import logging
from typing import Final

log = logging.getLogger("goat2.config.agents")

__all__ = [
    "AGENT_ROLES",
    "EXECUTION_ROLES",
    "SYNTHESIS_ROLES",
    "DEFAULT_AGENT_ROLE",
]

# All valid agent roles in GOAT 2.0
AGENT_ROLES: Final[list[str]] = [
    "researcher",
    "coder",
    "critic",
    "planner",
    "summarizer",
    "tool_caller",
    "memory",
]
"""Complete list of valid agent roles for task decomposition.

Used by supervisor/planner.py to validate role assignments in plans.
"""

# Roles that MUST invoke a real tool — generated output is never acceptable
EXECUTION_ROLES: Final[frozenset[str]] = frozenset({"researcher", "tool_caller", "memory"})
"""Roles that require tool invocation.

The DAG validator (supervisor/dag_validator.py) marks these as UNVERIFIED
if tool_called=False. Tool calls are enforced via tool_choice='required'.
"""

# Roles that generate content without external tool calls
SYNTHESIS_ROLES: Final[frozenset[str]] = frozenset({"summarizer", "critic", "planner"})
"""Roles where source=generated is valid.

These roles produce content via LLM inference without calling external tools.
The DAG validator allows source=generated for these roles.
"""

# Default role when none specified
DEFAULT_AGENT_ROLE: Final[str] = "tool_caller"
"""Default role for agent tasks.

Used as fallback when role is not specified in task decomposition.
tool_caller has full tool access and is the most general-purpose role.
"""