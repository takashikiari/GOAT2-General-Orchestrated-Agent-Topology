"""tests.test_log_tail — utils.logging.tail.tail_log, the shared log-tail helper
used by both the get_recent_logs tool and the admin panel's /api/logs route."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from utils.logging.tail import tail_log


def _write_log(path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n")


def _line(minutes_ago: int, level: str, msg: str) -> str:
    ts = (datetime.now() - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%S")
    return f"{ts}  some.module  {level:<8}  {msg}"


def test_returns_lines_within_window(tmp_path):
    path = tmp_path / "goat2.log"
    _write_log(path, [_line(5, "INFO", "recent"), _line(120, "INFO", "too old")])
    lines = tail_log(path, minutes=30, level="ALL", limit=100, max_lines=500)
    assert len(lines) == 1
    assert "recent" in lines[0]


def test_filters_by_level(tmp_path):
    path = tmp_path / "goat2.log"
    _write_log(path, [_line(1, "INFO", "info line"), _line(1, "ERROR", "error line")])
    lines = tail_log(path, minutes=30, level="ERROR", limit=100, max_lines=500)
    assert len(lines) == 1
    assert "error line" in lines[0]


def test_caps_at_limit_keeping_newest(tmp_path):
    path = tmp_path / "goat2.log"
    _write_log(path, [_line(3, "INFO", "first"), _line(2, "INFO", "second"), _line(1, "INFO", "third")])
    lines = tail_log(path, minutes=30, level="ALL", limit=2, max_lines=500)
    assert len(lines) == 2
    assert "second" in lines[0] and "third" in lines[1]


def test_limit_is_capped_by_max_lines(tmp_path):
    path = tmp_path / "goat2.log"
    _write_log(path, [_line(1, "INFO", f"line{i}") for i in range(5)])
    lines = tail_log(path, minutes=30, level="ALL", limit=1000, max_lines=3)
    assert len(lines) == 3


def test_missing_file_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        tail_log(tmp_path / "nope.log", minutes=30, level="ALL", limit=100, max_lines=500)


def test_unknown_level_raises_value_error(tmp_path):
    path = tmp_path / "goat2.log"
    _write_log(path, [_line(1, "INFO", "x")])
    with pytest.raises(ValueError):
        tail_log(path, minutes=30, level="BOGUS", limit=100, max_lines=500)
