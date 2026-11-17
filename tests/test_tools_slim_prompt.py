"""GOAT's prompt carries fewer tools and the tool loop has a real round cap (Laris, 2026-11-17).

- config/tools.toml [prompt] hidden: the 11 tools never called in 14-17.11 are neither offered nor callable;
  cancel_scheduled (pairs with schedule_message) stays;
- [tool_loop] max_iterations is 8 again (the 200 of 2026-08-14 was a test never reverted).
"""
from __future__ import annotations

import tomllib
from pathlib import Path

from orchestrator import orchestrator as orch_mod
from orchestrator.orchestrator import Orchestrator
from orchestrator.tool_loop_config import AGENTIC_MAX_ITERATIONS, HIDDEN_TOOLS
from orchestrator.tools import ToolDefinition
from tests._orch_fakes import _Completions, _FakeAnalytics, _FakeLayers, _FakePluginManager, _LLMClient

_CFG = tomllib.load(open(Path(__file__).resolve().parents[1] / "config" / "tools.toml", "rb"))
_EXPECTED = {"write_file", "start_workflow", "workflow_send", "workflow_status", "stop_workflow", "set_identity",
             "browse_page", "forget_fact", "fetch_content", "repo_list", "read_l1"}


def _tool(name):
    async def handler(**kw):
        return "ok"
    return ToolDefinition(name=name, description=name, parameters={"type": "object", "properties": {}},
                          handler=handler)


def test_the_config_hides_exactly_the_unused_tools():
    assert set(_CFG["prompt"]["hidden"]) == _EXPECTED == HIDDEN_TOOLS
    assert "cancel_scheduled" not in HIDDEN_TOOLS and "search_memory" not in HIDDEN_TOOLS


def test_the_round_cap_is_real_again():
    assert _CFG["tool_loop"]["max_iterations"] == 8 == AGENTIC_MAX_ITERATIONS


def test_hidden_tools_are_neither_offered_nor_callable(monkeypatch):
    monkeypatch.setattr(orch_mod, "HIDDEN_TOOLS", frozenset({"write_file", "start_workflow"}))

    plugins = _FakePluginManager()
    plugins.tools = [_tool("write_file"), _tool("repo_git")]
    o = Orchestrator(_FakeLayers(), _LLMClient(_Completions("ok")), plugins, _FakeAnalytics(),
                     tools=[_tool("search_memory"), _tool("start_workflow"), _tool("cancel_scheduled")])
    names = [t.name for t in o._all_tools()]
    assert names == ["search_memory", "cancel_scheduled", "repo_git"]
    assert not o._has_tool("write_file") and o._has_tool("repo_git")
