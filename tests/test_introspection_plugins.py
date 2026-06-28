"""tests.test_introspection_plugins — get_memory_metrics + get_recent_logs."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import tools.goat_skills.get_memory_metrics as metrics_mod
import tools.goat_skills.get_recent_logs as logs_mod


class _Analytics:
    def __init__(self, report: dict) -> None:
        self._report = report

    def get_report(self) -> dict:
        return self._report


class _Reg:
    def __init__(self, report: dict) -> None:
        self.memory_analytics = _Analytics(report)


def test_metrics_returns_report() -> None:
    report = {"total_requests": 7, "cache_hit_rate": 0.5, "avg_latency_total": 1.2}
    tool = metrics_mod.build(_Reg(report))[0]
    assert tool.name == "get_memory_metrics"
    out = json.loads(__import__("asyncio").run(tool.handler(chat_id="x")))
    assert out["total_requests"] == 7
    assert out["cache_hit_rate"] == 0.5


def _log_line(level: str, msg: str, when: datetime) -> str:
    return f"{when:%Y-%m-%dT%H:%M:%S}  some.logger  {level:<8s}  {msg}"


def test_logs_window_and_level(monkeypatch, tmp_path: Path) -> None:
    log_file = tmp_path / "goat2.log"
    now = datetime.now()
    log_file.write_text("\n".join([
        _log_line("INFO", "old", now - timedelta(hours=2)),
        _log_line("INFO", "recent-info", now - timedelta(minutes=2)),
        _log_line("WARNING", "recent-warn", now - timedelta(minutes=1)),
    ]) + "\n")
    monkeypatch.setattr(logs_mod, "LOG_FILE", log_file)
    tool = logs_mod.build(_Reg({}))[0]
    out = __import__("asyncio").run(tool.handler(minutes=30, level="ALL"))
    assert "recent-info" in out and "recent-warn" in out and "old" not in out
    warn_only = __import__("asyncio").run(tool.handler(minutes=30, level="WARNING"))
    assert "recent-warn" in warn_only and "recent-info" not in warn_only


def test_logs_missing_file(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(logs_mod, "LOG_FILE", tmp_path / "nope.log")
    tool = logs_mod.build(_Reg({}))[0]
    out = __import__("asyncio").run(tool.handler(minutes=30))
    assert "not found" in out


def test_logs_unknown_level(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(logs_mod, "LOG_FILE", tmp_path / "x.log")
    tool = logs_mod.build(_Reg({}))[0]
    out = __import__("asyncio").run(tool.handler(level="BOGUS"))
    assert "unknown level" in out