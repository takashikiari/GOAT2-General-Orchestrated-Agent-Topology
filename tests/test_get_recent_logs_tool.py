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


def _tool_with_log(tmp_path, monkeypatch, lines):
    path = tmp_path / "goat2.log"
    path.write_text("\n".join(lines) + "\n")
    monkeypatch.setattr(mod, "LOG_FILE", path)
    return mod.build(_FakeRegistry())[0]


def test_handler_under_the_char_cap_is_unchanged(tmp_path, monkeypatch):
    tool = _tool_with_log(tmp_path, monkeypatch, [_line("one"), _line("two")])
    result = asyncio.run(tool.handler(minutes=30, limit=100))
    assert result == "\n".join([_line("one"), _line("two")]) and "omise" not in result


def test_handler_char_cap_keeps_newest_lines_and_notes_omitted(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "_MAX_CHARS", 1000)
    lines = [_line(f"msg-{i:03d} " + "x" * 90) for i in range(40)]  # ~130 chars each, ~5k total
    tool = _tool_with_log(tmp_path, monkeypatch, lines)
    result = asyncio.run(tool.handler(minutes=30, limit=500))
    assert len(result) <= 1000 + 60  # cap + the leading note
    assert result.startswith("[… ") and "linii mai vechi omise]" in result.splitlines()[0]
    assert "msg-039" in result and "msg-000" not in result
    kept = len(result.splitlines()) - 1
    assert f"[… {40 - kept} linii mai vechi omise]" == result.splitlines()[0]


def test_handler_cuts_a_single_newest_line_longer_than_the_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "_MAX_CHARS", 200)
    tool = _tool_with_log(tmp_path, monkeypatch, [_line("old"), _line("y" * 5000)])
    result = asyncio.run(tool.handler(minutes=30, limit=100))
    assert len(result) <= 200 + 80
    assert "[… 1 linii mai vechi omise]" in result and "linie tăiată" in result


def test_default_cap_keeps_a_worst_case_call_below_the_tool_loop_cap(tmp_path, monkeypatch):
    # 500 DEBUG lines of ~170 chars were enough to produce the 60-89k results behind the size-cap incidents.
    lines = [_line("z" * 160) for _ in range(500)]
    tool = _tool_with_log(tmp_path, monkeypatch, lines)
    result = asyncio.run(tool.handler(minutes=30, limit=500))
    assert len(result) < 20000
