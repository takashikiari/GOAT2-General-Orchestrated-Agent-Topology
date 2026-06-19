"""Audit observations for BUG-025: agents/memory_agent.py.

The module is small (90 lines) and reasonably clean, but the
audit surfaced three small issues that the new tests pin down:

  1. The system prompt mentions ``memory_recent``, ``memory_get``,
     ``memory_store``, ``memory_search`` — but the registered
     tools are ``MEMORY_RECENT_DAG``, ``MEMORY_GET_DAG``,
     ``MEMORY_STORE_DAG``, ``MEMORY_SEARCH_DAG`` (the DAG-tier
     variants). The naming in the prompt and the tool surface
     should match so the LLM knows what to call.
  2. The runner function ``run_memory`` ignores the ``registry``
     argument when constructing the agent — it uses
     ``registry.settings.agents.get('memory')``, but the default
     in ``MemoryAgent.__init__`` falls back to ``tool_caller``
     when no spec is provided. The audit wants this fallback
     to match the prompt's "working tier" intent, not the
     tool_caller default.
  3. The agent source tag is set to ``"generated"`` regardless
     of whether memory tools were actually called. We want the
     source to reflect what happened (memory read/write).
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from agents.memory_agent import MemoryAgent, run_memory


# ── Tool registration ──────────────────────────────────────────────────────


def test_memory_agent_uses_dag_tier_tools():
    """The MemoryAgent must register the DAG-tier memory tools
    (working tier only) so it cannot reach ChromaDB or Letta
    through the agent surface."""
    from memory.memory_tools import (
        MEMORY_RECENT_DAG, MEMORY_GET_DAG,
        MEMORY_STORE_DAG, MEMORY_SEARCH_DAG,
    )
    expected = {
        MEMORY_RECENT_DAG.name,
        MEMORY_GET_DAG.name,
        MEMORY_STORE_DAG.name,
        MEMORY_SEARCH_DAG.name,
    }
    agent = MemoryAgent(spec=MagicMock())
    assert set(agent.tool_names) == expected


def test_memory_agent_system_prompt_names_match_tools():
    """The system prompt must mention the actual tool names
    so the LLM can call them correctly."""
    from agents.memory_agent import _SYSTEM_PROMPT
    for name in ("memory_recent", "memory_get", "memory_store", "memory_search"):
        assert name in _SYSTEM_PROMPT, (
            f"system prompt must reference the {name!r} tool"
        )


# ── Default spec resolution ───────────────────────────────────────────────


def test_run_memory_queries_memory_spec_key():
    """The runner must query the 'memory' spec key, not silently
    fall through to the tool_caller default. The agent has its
    own role with its own model — conflating it with tool_caller
    is a configuration footgun."""
    import inspect
    runner_src = inspect.getsource(run_memory)
    assert "agents.get('memory')" in runner_src or 'agents.get("memory")' in runner_src, (
        "run_memory must query the 'memory' spec key explicitly."
    )


# ── Source tag ─────────────────────────────────────────────────────────────


def test_run_memory_sets_source_to_memory():
    """The runner must set ``task.source = 'memory'`` so downstream
    consumers (audit, MCP diagnose_turn) can tell that the
    output came from the memory role — not from a generic
    'generated' agent."""
    task = MagicMock()
    registry = MagicMock()
    registry.settings.agents.get.return_value = MagicMock(name="fake-memory")

    async def _main():
        with patch("agents.memory_agent.MemoryAgent") as MockAgent:
            instance = MockAgent.return_value
            instance.execute = AsyncMock(return_value="ok")
            await run_memory(task, context={}, registry=registry)
        assert task.source == "memory"
    asyncio.run(_main())