"""admin_panel.routes.voice — receives a recorded voice message from the chat page.

Validates the upload, then transcribes it with Deepgram (pre-recorded API) when ``DEEPGRAM_API_KEY`` is configured on the
server; without a key it answers an explicit stub (``stub: true``, empty ``text``) so the page can say so.
Auth is the same Telegram initData check as the other routes that act (``require_admin_write_auth``).

The body is read by hand, AFTER auth and a size check, instead of declaring ``UploadFile`` parameters: FastAPI
parses declared body parameters before it resolves dependencies, so an unauthenticated client could make the server
spool a large upload to disk before getting its 401.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.datastructures import UploadFile

from admin_panel import deepgram, voice_config
from admin_panel.voice_live import providers
from admin_panel.voice_live.usage import usage_store
from admin_panel.telegram_auth import require_admin_write_auth

router = APIRouter()

__all__ = ["router", "MAX_AUDIO_BYTES", "MIN_DURATION_MS", "MAX_DURATION_MS"]

# 60 s of Opus is about 0.5 MB and AAC about 1 MB: 5 MiB leaves plenty of headroom and still bounds the upload.
MAX_AUDIO_BYTES = 5 * 1024 * 1024
# multipart framing around the file and the duration field
_OVERHEAD_BYTES = 64 * 1024
# the page records for at most 60 s; the extra 5 s is slack for the timer and the stop latency
MIN_DURATION_MS = 300
MAX_DURATION_MS = 65_000


def _looks_like_audio(head: bytes) -> bool:
    """The container formats a browser's MediaRecorder produces (and the usual uploads): do not trust the header alone."""
    return (
        head.startswith(b"\x1a\x45\xdf\xa3")  # webm / matroska (EBML)
        or head[4:8] == b"ftyp"  # mp4 / m4a
        or head.startswith(b"OggS")  # ogg
        or (head.startswith(b"RIFF") and head[8:12] == b"WAVE")  # wav
        or head.startswith(b"ID3")  # mp3 with tags
        or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0)  # mp3 / aac frame sync
    )


@router.post("/api/voice", dependencies=[Depends(require_admin_write_auth)])
async def voice(request: Request) -> dict:
    declared = request.headers.get("content-length")
    if declared is None:
        raise HTTPException(status_code=411, detail="Content-Length required")
    try:
        declared_bytes = int(declared)
    except ValueError:
        raise HTTPException(status_code=400, detail="bad Content-Length")
    if declared_bytes > MAX_AUDIO_BYTES + _OVERHEAD_BYTES:
        raise HTTPException(status_code=413, detail=f"audio larger than {MAX_AUDIO_BYTES} bytes")

    form = await request.form(max_files=1, max_fields=4)
    audio = form.get("audio")
    if not isinstance(audio, UploadFile):
        raise HTTPException(status_code=422, detail="missing audio file")
    content_type = (audio.content_type or "").split(";")[0].strip().lower()
    if not content_type.startswith("audio/"):
        raise HTTPException(status_code=422, detail="unsupported content type (audio/* expected)")
    try:
        duration_ms = int(str(form.get("duration_ms", "")).strip())
    except ValueError:
        raise HTTPException(status_code=422, detail="duration_ms must be an integer")
    if not MIN_DURATION_MS <= duration_ms <= MAX_DURATION_MS:
        raise HTTPException(status_code=422, detail=f"duration_ms must be between {MIN_DURATION_MS} and {MAX_DURATION_MS}")

    data = await audio.read(MAX_AUDIO_BYTES + 1)
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail=f"audio larger than {MAX_AUDIO_BYTES} bytes")
    if not data:
        raise HTTPException(status_code=422, detail="empty audio")
    if not _looks_like_audio(data[:16]):
        raise HTTPException(status_code=422, detail="the file does not look like audio")

    if voice_config.api_key() is None:
        # no provider configured on this server: say so instead of pretending (the page shows a notice)
        return {
            "ok": True,
            "stub": True,
            "duration_ms": duration_ms,
            "bytes": len(data),
            "content_type": content_type,
            "text": "",
        }
    try:
        text = await deepgram.transcribe(data, content_type)
    except deepgram.DeepgramError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {
        "ok": True,
        "stub": False,
        "duration_ms": duration_ms,
        "bytes": len(data),
        "content_type": content_type,
        "text": text,
    }


@router.get("/api/voice/live/status")
async def live_status() -> dict:
    """Whether the speech-to-speech conversation can be started on this server (the page offers "Live" only then), and
    whether today's minutes are used up (the page then talks with the Deepgram voice instead)."""
    return {
        "available": providers.is_available(voice_config.live_provider()),
        "limit_reached": usage_store().left_seconds(voice_config.live_daily_minutes()) <= 0,
    }


@router.get("/api/voice/live/usage")
async def live_usage() -> dict:
    """Today's live voice minutes for the Dashboard."""
    seconds, sessions = usage_store().today()
    limit = voice_config.live_daily_minutes()
    return {
        "provider": voice_config.live_provider(),
        "today_minutes": round(seconds / 60, 1),
        "limit_minutes": limit,
        "sessions_today": sessions,
        "limit_reached": seconds >= limit * 60,
    }
