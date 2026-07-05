"""GOAT 2.0 — agents package.

Each agent is a submodule (``agents.planner``, ``agents.coder``,
``agents.critic``, ...). Import the specific submodule directly:

    from agents.planner import PlannerAgent
    from agents.researcher import ResearcherAgent

For DAG execution, use ``workflow.routing.AgentRouter`` which resolves
roles to agent instances lazily and caches them per router instance.
"""
from __future__ import annotations

__all__: list[str] = []
