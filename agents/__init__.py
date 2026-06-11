"""GOAT 2.0 — agents package.

Re-exports the 7 built-in agent classes, the BaseAgent primitives,
the legacy critique helpers, and the researcher system prompt.

DEPENDENCY MANAGEMENT (routing + TYPE_CHECKING + Registry):
==========================================================
agents/ must NEVER import from supervisor/ at module level — see
config/routing.py and the TYPE_CHECKING guards inside each agent file.
The supervisor side reaches back into agents/ via the AgentRegistry
(``supervisor.registry._build_default_registry``) at runtime.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

# Cross-module types are checked only — keeps agents/ decoupled at runtime.
if TYPE_CHECKING:
    from config.agent_types import AgentResult, AgentTask
    from config.registry import Registry

from .base_agent import BaseAgent, ToolDefinition, tool
from .planner import PlannerAgent
from .planner_decompose import _run_planner
from .researcher import ResearcherAgent
from .coder import CoderAgent
from .critic import CriticAgent
from .summarizer import SummarizerAgent
from .tool_caller import ToolCallerAgent
from .memory_agent import MemoryAgent
from .prompts import RESEARCHER_SYSTEM
from .critique import critique_results, synthesize_results, CriticVerdict, parse_verdict

__all__ = [
    "BaseAgent",
    "ToolDefinition",
    "tool",
    "PlannerAgent",
    "_run_planner",
    "ResearcherAgent",
    "CoderAgent",
    "CriticAgent",
    "SummarizerAgent",
    "ToolCallerAgent",
    "MemoryAgent",
    "RESEARCHER_SYSTEM",
    "critique_results",
    "synthesize_results",
    "CriticVerdict",
    "parse_verdict",
]
