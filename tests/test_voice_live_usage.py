"""tests.test_voice_live_usage — the live voice's daily minutes: counted, kept across restarts, capped, shown."""
from __future__ import annotations

import json
from datetime import datetime

from admin_panel import voice_config
from admin_panel.voice_live.usage import LiveUsage
from voice_live_helpers import FRAME, _headers, _live, _until, env  # noqa: F401 - env is a fixture


def _clock(day: str):
    return lambda: datetime.fromisoformat(f"{day}T12:00:00").timestamp()


def test_seconds_and_sessions_add_up_per_day_and_survive_a_restart(tmp_path):
    path = tmp_path / "usage.json"
    u = LiveUsage(path, clock=_clock("2026-11-10"))
    u.add(30, session=True)
    u.add(45.5)
    assert u.today() == (75.5, 1)
    again = LiveUsage(path, clock=_clock("2026-11-10"))  # GOAT restarted
    assert again.today() == (75.5, 1)
    assert LiveUsage(path, clock=_clock("2026-11-11")).today() == (0.0, 0)  # a new day starts at zero


def test_old_days_are_dropped_and_a_broken_file_starts_fresh(tmp_path):
    path = tmp_path / "usage.json"
    for day in range(1, 32):
        LiveUsage(path, clock=_clock(f"2026-10-{day:02d}")).add(60, session=True)
    LiveUsage(path, clock=_clock("2026-11-10")).add(1)
    days = json.loads(path.read_text())["days"]
    assert len(days) <= 31 and "2026-10-01" not in days and "2026-11-10" in days
    path.write_text("{not json")
    assert LiveUsage(path, clock=_clock("2026-11-10")).today() == (0.0, 0)


def test_a_file_that_cannot_be_written_never_breaks_a_conversation(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    u = LiveUsage(blocker / "usage.json")  # its directory is a file: cannot be created
    u.add(10, session=True)
    assert u.today() == (10.0, 1)  # still counted in memory


def test_the_daily_limit_comes_from_the_environment(monkeypatch):
    monkeypatch.delenv("VOICE_LIVE_DAILY_MINUTES", raising=False)
    assert voice_config.live_daily_minutes() == 60
    monkeypatch.setenv("VOICE_LIVE_DAILY_MINUTES", "15")
    assert voice_config.live_daily_minutes() == 15
    monkeypatch.setenv("VOICE_LIVE_DAILY_MINUTES", "zero")
    assert voice_config.live_daily_minutes() == 60


def test_a_conversation_is_counted(env):
    with _live(env) as s:
        _until(s, lambda m: m["type"] == "state")
        s.send_bytes(FRAME)
        s.send_json({"type": "close"})
        _until(s, lambda m: m["type"] == "closed")
    seconds, sessions = env.usage().today()
    assert sessions == 1 and seconds > 0


def test_no_conversation_starts_once_the_days_minutes_are_used_up(env):
    env.monkeypatch.setenv("VOICE_LIVE_DAILY_MINUTES", "1")
    env.usage().add(60, session=True)
    with _live(env) as s:
        error = _until(s, lambda m: m["type"] == "error")[-1]
    assert error["code"] == "daily_limit" and env.made == []  # the provider was never opened


def test_a_conversation_ends_when_it_reaches_the_days_limit(env):
    env.monkeypatch.setenv("VOICE_LIVE_DAILY_MINUTES", "1")
    env.usage().add(59.8)
    with _live(env) as s:
        _until(s, lambda m: m["type"] == "state")
        error = _until(s, lambda m: m["type"] == "error")[-1]
    assert error["code"] == "daily_limit"
    assert env.usage().today()[0] >= 60


def test_the_dashboard_reads_todays_minutes_and_the_status_says_when_the_limit_is_reached(env):
    assert env.client.get("/api/voice/live/usage").status_code == 401
    env.monkeypatch.setenv("VOICE_LIVE_DAILY_MINUTES", "2")
    env.usage().add(90, session=True)
    body = env.client.get("/api/voice/live/usage", headers=_headers()).json()
    assert body == {"provider": "fake", "today_minutes": 1.5, "limit_minutes": 2, "sessions_today": 1,
                    "limit_reached": False}
    assert env.client.get("/api/voice/live/status", headers=_headers()).json() == {"available": True, "limit_reached": False}
    env.usage().add(30)
    assert env.client.get("/api/voice/live/status", headers=_headers()).json() == {"available": True, "limit_reached": True}
