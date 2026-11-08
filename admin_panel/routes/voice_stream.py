"""admin_panel.routes.voice_stream — live speech-to-text for the chat's conversation mode (WebSocket proxy).

The browser streams 16 kHz mono 16-bit PCM here; this route forwards it to Deepgram's streaming API and hands back
simple events. The Deepgram key stays on the server: the browser never sees it.

Auth: the first message from the page is checked before anything is forwarded (see ``admin_panel.voice_ws``).

Messages to the page (JSON text frames):
  {"type": "ready"}                                              Deepgram is connected, start sending audio
  {"type": "transcript", "text", "final": bool, "speech_final": bool}
  {"type": "speech_started"} / {"type": "utterance_end"}
  {"type": "error", "code", "message"}                           then the socket closes
From the page: binary frames (PCM) and ``{"type": "close"}``.
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus, WebSocketException

from admin_panel import voice_config
from admin_panel.voice_ws import authenticate, send_error as _error
from utils.logging.setup import get_logger

router = APIRouter()
log = get_logger(__name__)

__all__ = ["router", "AUTH_TIMEOUT_SECONDS", "MAX_FRAME_BYTES"]

AUTH_TIMEOUT_SECONDS = 10.0
MAX_FRAME_BYTES = 64 * 1024  # 2 s of 16 kHz 16-bit mono is 64 KB; the page sends ~0.25 s at a time


def _event_for(raw: str) -> dict | None:
    """Deepgram's message as the simple event the page understands (None = nothing to tell the page)."""
    try:
        msg = json.loads(raw)
    except ValueError:
        return None
    kind = msg.get("type")
    if kind == "Results":
        alternatives = (msg.get("channel") or {}).get("alternatives") or [{}]
        text = (alternatives[0].get("transcript") or "").strip()
        final, speech_final = bool(msg.get("is_final")), bool(msg.get("speech_final"))
        if not text and not speech_final:
            return None
        return {"type": "transcript", "text": text, "final": final, "speech_final": speech_final}
    if kind == "UtteranceEnd":
        return {"type": "utterance_end"}
    if kind == "SpeechStarted":
        return {"type": "speech_started"}
    return None


@router.websocket("/api/voice/stream")
async def voice_stream(ws: WebSocket) -> None:
    await ws.accept()
    if not await authenticate(ws, AUTH_TIMEOUT_SECONDS):
        return

    key = voice_config.api_key()
    if key is None:
        await _error(ws, "not_configured", "Deepgram is not configured on the server (DEEPGRAM_API_KEY is missing)")
        return

    try:
        upstream = await connect(
            voice_config.ws_listen_url(), additional_headers={"Authorization": f"Token {key}"}, open_timeout=10,
            max_size=2**20,
        )
    except InvalidStatus as exc:
        status = exc.response.status_code
        log.warning("voice_stream: Deepgram refused the connection (HTTP %s)", status)
        if status in (401, 403):
            await _error(ws, "upstream_auth", "Deepgram rejected the API key")
        else:
            await _error(ws, "upstream", f"Deepgram refused the connection (HTTP {status})")
        return
    except (OSError, WebSocketException, asyncio.TimeoutError) as exc:
        log.warning("voice_stream: could not reach Deepgram: %s", type(exc).__name__)
        await _error(ws, "upstream", "could not reach Deepgram")
        return

    last_sent = asyncio.get_event_loop().time()

    async def page_to_upstream() -> None:
        nonlocal last_sent
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                return
            if message.get("bytes") is not None:
                data = message["bytes"]
                if len(data) > MAX_FRAME_BYTES:
                    await _error(ws, "frame_too_large", "audio frame too large", 1009)
                    return
                await upstream.send(data)
                last_sent = asyncio.get_event_loop().time()
            elif message.get("text") is not None:
                try:
                    control = json.loads(message["text"])
                except ValueError:
                    continue
                if isinstance(control, dict) and control.get("type") == "close":
                    await upstream.send(json.dumps({"type": "CloseStream"}))
                    return

    async def upstream_to_page() -> None:
        async for raw in upstream:
            if isinstance(raw, bytes):
                continue
            event = _event_for(raw)
            if event is not None:
                await ws.send_json(event)

    async def keepalive() -> None:
        nonlocal last_sent
        while True:
            await asyncio.sleep(voice_config.KEEPALIVE_SECONDS / 2)
            if asyncio.get_event_loop().time() - last_sent >= voice_config.KEEPALIVE_SECONDS:
                await upstream.send(json.dumps({"type": "KeepAlive"}))
                last_sent = asyncio.get_event_loop().time()

    tasks = [asyncio.create_task(page_to_upstream()), asyncio.create_task(upstream_to_page()), asyncio.create_task(keepalive())]
    try:
        await ws.send_json({"type": "ready"})
        done, _ = await asyncio.wait(tasks, timeout=voice_config.MAX_STREAM_SECONDS, return_when=asyncio.FIRST_COMPLETED)
        if not done:
            await _error(ws, "time_limit", "the conversation reached its time limit", 1000)
        else:
            ended_by_upstream = tasks[1] in done and tasks[0] not in done
            failure = next((t.exception() for t in done if not t.cancelled() and t.exception()), None)
            if failure is not None and not isinstance(failure, (ConnectionClosed, WebSocketDisconnect, RuntimeError)):
                log.warning("voice_stream: %s", type(failure).__name__)
            if ended_by_upstream:
                await _error(ws, "upstream", "the speech recognition connection closed")
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await upstream.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass
