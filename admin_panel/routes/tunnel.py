"""admin_panel.routes.tunnel — the Cloudflare tunnel link the panel is currently reachable on."""
from __future__ import annotations

from fastapi import APIRouter

from admin_panel.tunnel_state import get_state

router = APIRouter()

__all__ = ["router"]


@router.get("/api/tunnel")
async def tunnel() -> dict:
    return get_state()
