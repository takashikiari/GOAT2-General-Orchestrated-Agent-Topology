"""tests.test_admin_panel_logs — /api/logs route over a temp log file."""
from __future__ import annotations

from datetime import datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

import admin_panel.routes.logs as logs_route


def _line(msg: str) -> str:
    ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    return f"{ts}  some.module  INFO      {msg}"


def _client():
    app = FastAPI()
    app.include_router(logs_route.router)
    return TestClient(app)


def test_get_logs_returns_lines(tmp_path, monkeypatch):
    path = tmp_path / "goat2.log"
    path.write_text(_line("hello") + "\n")
    monkeypatch.setattr(logs_route, "LOG_FILE", path)
    resp = _client().get("/api/logs")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["lines"]) == 1
    assert "hello" in body["lines"][0]


def test_get_logs_missing_file_returns_error(tmp_path, monkeypatch):
    monkeypatch.setattr(logs_route, "LOG_FILE", tmp_path / "missing.log")
    resp = _client().get("/api/logs")
    assert resp.status_code == 200
    assert "error" in resp.json()


def test_get_logs_unknown_level_returns_error(tmp_path, monkeypatch):
    path = tmp_path / "goat2.log"
    path.write_text(_line("hi") + "\n")
    monkeypatch.setattr(logs_route, "LOG_FILE", path)
    resp = _client().get("/api/logs?level=BOGUS")
    assert resp.status_code == 200
    assert "error" in resp.json()
