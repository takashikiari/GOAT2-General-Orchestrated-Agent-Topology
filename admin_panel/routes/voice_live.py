"""admin_panel.routes.voice_live — the speech-to-speech voice conversation (WebSocket ``/api/voice/live``).

Auth is the first message from the page (see ``admin_panel.voice_ws``); nothing is opened before that succeeds. The
provider is chosen by ``VOICE_LIVE_PROVIDER`` and its key stays on the server. The protocol is in
``admin_panel.voice_live.session``.
"""
from __future__ import annotations

from fastapi import APIRouter, WebSocket

from admin_panel import voice_config
from admin_panel.voice_live import providers, session
from admin_panel.voice_live.bridge import GoatBridge, recent_history
from admin_panel.voice_ws import authenticate, send_error
from utils.logging.setup import get_logger

router = APIRouter()
log = get_logger(__name__)

__all__ = ["router", "AUTH_TIMEOUT_SECONDS"]

AUTH_TIMEOUT_SECONDS = 10.0


@router.websocket("/api/voice/live")
async def voice_live(ws: WebSocket) -> None:
    await ws.accept()
    if not await authenticate(ws, AUTH_TIMEOUT_SECONDS):
        return
    provider = providers.make_provider(voice_config.live_provider())
    if provider is None:
        await send_error(ws, "not_configured", "the live voice is not set up on this server")
        return
    registry = ws.app.state.registry
    service = getattr(registry, "chat_service", None)
    if service is None:
        await send_error(ws, "chat_unavailable", "GOAT's chat is not available right now")
        return
    history = await recent_history(registry)
    try:
        reason = await session.run_session(
            ws, provider, GoatBridge(service), session.default_config(voice_config.live_voice()), history,
            idle_seconds=voice_config.LIVE_IDLE_SECONDS, max_seconds=voice_config.LIVE_MAX_SECONDS,
        )
        log.info("voice_live: ended (%s)", reason)
    except Exception as exc:  # noqa: BLE001 - tell the page, never leave it hanging
        log.warning("voice_live: failed: %s", type(exc).__name__)
        await send_error(ws, "upstream", "the voice connection failed")
    finally:
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass
