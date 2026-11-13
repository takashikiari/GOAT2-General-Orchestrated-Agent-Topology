"""admin_panel.routes.voice_message — a spoken message to GOAT: record, send, get the answer read aloud (Laris, 2026-11-13).

The recording is validated exactly like ``/api/voice`` (``read_clip``: auth first, size, type, duration), transcribed
by Gemini (the ear only, ``admin_panel.gemini_stt``), and the transcript becomes a normal GOAT turn through
``ChatService.submit``: the same orchestrator, chat, per-chat lock and memory as a typed or Telegram message, marked
``source="voice_live"``, with ``said_at`` (when Laris started speaking) and ``voice=True`` (a reply written to be read
aloud). The page polls the turn on ``/api/chat/turns/{id}`` like any message and reads the reply with the existing
Deepgram voice (``/api/voice/speak/stream``). The voice layer keeps no memory of its own.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request

from admin_panel import gemini_stt
from admin_panel.routes.voice import read_clip
from admin_panel.telegram_auth import require_admin_write_auth
from orchestrator.chat_service import ChatBusy, ChatUnavailable
from utils.logging.setup import get_logger

log = get_logger(__name__)

router = APIRouter()

__all__ = ["router", "SOURCE", "clamp_said_at"]

SOURCE = "voice_live"
_SAID_AT_SLACK_SECONDS = 60.0


def clamp_said_at(raw: object, duration_ms: int, now: float | None = None) -> float:
    """When Laris started speaking (epoch seconds, from the page's clock). Not in the future, and not older than the
    clip plus a minute; anything missing or unreadable is "now minus the clip's length"."""
    now = time.time() if now is None else now
    earliest = now - duration_ms / 1000 - _SAID_AT_SLACK_SECONDS
    fallback = now - duration_ms / 1000
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return fallback
    if value != value:  # NaN
        return fallback
    return min(now, max(earliest, value))


@router.post("/api/voice/message", dependencies=[Depends(require_admin_write_auth)])
async def voice_message(request: Request) -> dict:
    clip = await read_clip(request)
    said_at = clamp_said_at(clip.form.get("said_at"), clip.duration_ms)
    service = getattr(request.app.state.registry, "chat_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="chat not available")
    started = time.monotonic()
    try:
        transcript = await gemini_stt.transcribe(clip.data)
    except gemini_stt.SttError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    stt_ms = int((time.monotonic() - started) * 1000)
    if not transcript:
        log.info("voice_message: nothing recognised stt_ms=%d duration_ms=%d", stt_ms, clip.duration_ms)
        return {"turn_id": None, "transcript": ""}
    try:
        turn = service.submit(transcript, source=SOURCE, said_at=said_at, voice=True)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ChatBusy:
        raise HTTPException(status_code=429, detail="too many messages waiting")
    except ChatUnavailable:
        raise HTTPException(status_code=503, detail="chat not available")
    log.info("voice_message: turn=%s stt_ms=%d duration_ms=%d chars=%d", turn.id, stt_ms, clip.duration_ms,
             len(transcript))
    return {"turn_id": turn.id, "transcript": transcript}
