"""admin_panel.routes.metrics — read-only /api/metrics over the live MemoryAnalytics report."""
from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/api/metrics")
async def get_metrics(request: Request) -> dict:
    registry = request.app.state.registry
    return registry.memory_analytics.get_report()
