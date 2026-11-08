"""admin_panel.voice_ws — what the two voice WebSockets (listening and speaking) have in common.

A browser cannot put a header on a WebSocket handshake, so these routes are mounted WITHOUT the header-based dependency
the other routes use. The first message the page sends is ``{"type": "auth", "initData": "..."}``, checked with the same
Telegram initData verification and the same freshness limit as the other routes that act; nothing is forwarded and no
Deepgram connection is opened before that succeeds.
"""
from __future__ import annotations

import asyncio

from fastapi import HTTPException, WebSocket, WebSocketDisconnect

from admin_panel.telegram_auth import authorize_write

__all__ = ["send_error", "authenticate"]


async def send_error(ws: WebSocket, code: str, message: str, close_code: int = 1011) -> None:
    """``{"type": "error", "code", "message"}`` to the page, then close."""
    try:
        await ws.send_json({"type": "error", "code": code, "message": message})
        await ws.close(code=close_code)
    except Exception:  # noqa: BLE001 - the page may already be gone
        pass


async def authenticate(ws: WebSocket, timeout: float) -> bool:
    """Wait for the auth message and verify it. On failure the page has been told and the socket closed (returns False)."""
    try:
        first = await asyncio.wait_for(ws.receive_json(), timeout=timeout)
    except (asyncio.TimeoutError, ValueError, WebSocketDisconnect, RuntimeError):
        await send_error(ws, "unauthorized", "authentication message missing", 4401)
        return False
    if not isinstance(first, dict) or first.get("type") != "auth" or not isinstance(first.get("initData"), str):
        await send_error(ws, "unauthorized", "authentication message missing", 4401)
        return False
    try:
        authorize_write(first["initData"])
    except HTTPException:
        await send_error(ws, "unauthorized", "session expired: reopen the panel from the Telegram menu button", 4401)
        return False
    return True
