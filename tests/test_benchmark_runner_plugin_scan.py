"""tests.test_benchmark_runner_plugin_scan — BenchmarkRunner scans plugins on
construction so its orchestrator sees the same tool_manager.tools (read_file,
shell, etc.) production gets from telegram_interface._plugin_scanner's
post_init_hook. Without this, every benchmark/diag run against a real
ServiceRegistry silently misses plugin tools, making cold-turn behavior look
worse than production.
"""
from __future__ import annotations

from orchestrator.tools import ToolDefinition
from benchmark.runner import BenchmarkRunner
from tests._orch_fakes import (
    _Completions, _FakeAnalytics, _FakeLayers, _FakePluginManager, _FakeRegistry, _LLMClient,
)


async def _handler(**kw) -> str:
    return "ok"


def test_benchmark_runner_scans_plugins_on_construction() -> None:
    fake_tool = ToolDefinition(name="read_file", description="d", parameters={}, handler=_handler)
    reg = _FakeRegistry(_FakeLayers(), _LLMClient(_Completions("r")), _FakeAnalytics())
    reg.plugin_manager = _FakePluginManager(tools_after_scan=[fake_tool])

    runner = BenchmarkRunner(registry=reg)

    assert reg.plugin_manager.scan_calls >= 1
    assert "read_file" in [t.name for t in runner._orchestrator._all_tools()]
