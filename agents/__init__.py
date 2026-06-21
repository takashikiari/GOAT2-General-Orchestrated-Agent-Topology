"""GOAT 2.0 — agents package.

Each agent is a submodule (``agents.planner``, ``agents.coder``,
``agents.critic``, ...). Callers should import the specific
submodule they need — the package re-exports were removed
because no production code used them (only the registry
loads agents via ``importlib.import_module`` with explicit
role names).

``AgentRegistry`` (in ``agents.registry``) is the canonical
runtime surface; it pre-registers the seven default agents
via lazy ``importlib`` imports so a single broken agent
does not block the others.
"""
from __future__ import annotations

__all__: list[str] = []
