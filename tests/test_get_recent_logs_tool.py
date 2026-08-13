"""tests.test_get_recent_logs_tool — goat_skills.get_recent_logs tool wraps
utils.logging.tail.tail_log into GOAT-facing message strings."""
from __future__ import annotations

import asyncio
from datetime import datetime

import tools.goat_skills.get_recent_logs as mod


class _FakeRegistry:
    pass


def _line(msg: str) -> str:
    ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    return f"{ts}  some.module  INFO      {msg}"


def test_handler_returns_matching_lines(tmp_path, monkeypatch):
    path = tmp_path / "goat2.log"
    path.write_text(_line("hello") + "\n")
    monkeypatch.setattr(mod, "LOG_FILE", path)
    tool = mod.build(_FakeRegistry())[0]
    result = asyncio.run(tool.handler(minutes=30, level="ALL", limit=100))
    assert "hello" in result


def test_handler_missing_file_message(tmp_path, monkeypatch):
    path = tmp_path / "missing.log"
    monkeypatch.setattr(mod, "LOG_FILE", path)
    tool = mod.build(_FakeRegistry())[0]
    result = asyncio.run(tool.handler())
    assert "log file not found" in result


def test_handler_unknown_level_message(tmp_path, monkeypatch):
    path = tmp_path / "goat2.log"
    path.write_text(_line("hi") + "\n")
    monkeypatch.setattr(mod, "LOG_FILE", path)
    tool = mod.build(_FakeRegistry())[0]
    result = asyncio.run(tool.handler(level="BOGUS"))
    assert "unknown level" in result


def test_handler_no_matches_message(tmp_path, monkeypatch):
    path = tmp_path / "goat2.log"
    path.write_text("")
    monkeypatch.setattr(mod, "LOG_FILE", path)
    tool = mod.build(_FakeRegistry())[0]
    result = asyncio.run(tool.handler(minutes=30))
    assert "no matching log lines" in result
