"""tests.test_plugin_manager — reconcile/add/reload/drop/failure-isolation."""
from __future__ import annotations

import os
import textwrap
from pathlib import Path

from orchestrator.tools import ToolDefinition
from plugins.plugin_manager import PluginManager

_BASE = """
from orchestrator.tools import ToolDefinition
def build(registry):
    async def handler(chat_id=""):
        return "ok-{name}"
    return [ToolDefinition(name="{name}", description="d", parameters={{}}, handler=handler)]
"""


def _write(path: Path, name: str) -> None:
    path.write_text(textwrap.dedent(_BASE).format(name=name))


def _manager(tmp_path: Path) -> PluginManager:
    return PluginManager(object(), tmp_path)


def _bump(path: Path) -> None:
    t = path.stat().st_mtime_ns + 10_000_000
    os.utime(path, ns=(t, t))


def test_add_plugin(tmp_path: Path) -> None:
    _write(tmp_path / "alpha.py", "alpha")
    pm = _manager(tmp_path); pm.scan()
    assert [t.name for t in pm.tools] == ["alpha"]


def test_reload_on_mtime_change(tmp_path: Path) -> None:
    _write(tmp_path / "beta.py", "beta")
    pm = _manager(tmp_path); pm.scan()
    assert [t.name for t in pm.tools] == ["beta"]
    _write(tmp_path / "beta.py", "beta2"); _bump(tmp_path / "beta.py")
    pm.scan()
    assert [t.name for t in pm.tools] == ["beta2"]


def test_delete_drops(tmp_path: Path) -> None:
    _write(tmp_path / "gamma.py", "gamma")
    pm = _manager(tmp_path); pm.scan()
    (tmp_path / "gamma.py").unlink(); pm.scan()
    assert pm.tools == []


def test_broken_import_skipped_others_unaffected(tmp_path: Path) -> None:
    _write(tmp_path / "good.py", "good")
    (tmp_path / "bad.py").write_text("raise RuntimeError('boom')\n")
    pm = _manager(tmp_path); pm.scan()
    assert [t.name for t in pm.tools] == ["good"]


def test_no_build_skipped(tmp_path: Path) -> None:
    (tmp_path / "nobuild.py").write_text("X = 1\n")
    pm = _manager(tmp_path); pm.scan()
    assert pm.tools == []


def test_build_wrong_type_skipped(tmp_path: Path) -> None:
    (tmp_path / "wrong.py").write_text("def build(registry):\n    return 'not a list'\n")
    pm = _manager(tmp_path); pm.scan()
    assert pm.tools == []


def test_keep_last_good_on_reload_break(tmp_path: Path) -> None:
    _write(tmp_path / "k.py", "k")
    pm = _manager(tmp_path); pm.scan()
    assert [t.name for t in pm.tools] == ["k"]
    (tmp_path / "k.py").write_text("def build(registry):\n    raise RuntimeError('x')\n")
    _bump(tmp_path / "k.py"); pm.scan()
    assert [t.name for t in pm.tools] == ["k"]