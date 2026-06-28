"""tests.test_orchestrator_plugins — orchestrator surfaces plugin tools + dispatch."""
from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest

from orchestrator.orchestrator import Orchestrator
from orchestrator.tools import ToolDefinition
from plugins.plugin_manager import PluginManager


_BASE = """
from orchestrator.tools import ToolDefinition
def build(registry):
    async def handler(chat_id=""):
        return "plug-{name}"
    return [ToolDefinition(name="{name}", description="d", parameters={{}}, handler=handler)]
"""


def _write(path: Path, name: str) -> None:
    path.write_text(textwrap.dedent(_BASE).format(name=name))


def _bump(path: Path) -> None:
    t = path.stat().st_mtime_ns + 10_000_000
    os.utime(path, ns=(t, t))


class _Reg:
    """Fake registry exposing only the plugin_manager (no backends needed)."""

    def __init__(self, plugins_dir: Path) -> None:
        self.plugin_manager = PluginManager(self, plugins_dir)


class _Func:
    def __init__(self, name: str, args: str) -> None:
        self.name = name
        self.arguments = args


class _TC:
    def __init__(self, name: str, args: str = "{}") -> None:
        self.id = "1"
        self.function = _Func(name, args)


def test_plugin_tool_visible_and_callable(tmp_path: Path) -> None:
    _write(tmp_path / "p.py", "p")
    reg = _Reg(tmp_path)
    reg.plugin_manager.scan()
    o = Orchestrator(reg, tools=[])
    names = [t.name for t in o._all_tools()]
    assert "p" in names
    assert o._has_tool("p")
    import asyncio
    out = asyncio.run(o._call_tool(_TC("p"), "chat", o._all_tools()))
    assert out == "plug-p"


def test_midrun_scan_adds_plugin(tmp_path: Path) -> None:
    reg = _Reg(tmp_path)
    reg.plugin_manager.scan()
    assert reg.plugin_manager.tools == []
    o = Orchestrator(reg, tools=[])
    assert [t.name for t in o._all_tools()] == []
    _write(tmp_path / "late.py", "late")
    reg.plugin_manager.scan()
    assert "late" in [t.name for t in o._all_tools()]