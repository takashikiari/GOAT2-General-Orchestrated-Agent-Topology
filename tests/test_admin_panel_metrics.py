"""tests.test_admin_panel_metrics — /api/metrics route over a fake MemoryAnalytics."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from admin_panel.routes.metrics import router


class _FakeAnalytics:
    def get_report(self):
        return {"total_requests": 5, "cache_hit_rate": 0.5}


class _FakeRegistry:
    def __init__(self):
        self.memory_analytics = _FakeAnalytics()


def _client():
    app = FastAPI()
    app.state.registry = _FakeRegistry()
    app.include_router(router)
    return TestClient(app)


def test_get_metrics_returns_report():
    resp = _client().get("/api/metrics")
    assert resp.status_code == 200
    assert resp.json() == {"total_requests": 5, "cache_hit_rate": 0.5}
