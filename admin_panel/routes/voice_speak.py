"""admin_panel.routes.voice_speak — GOAT's reply spoken aloud with Deepgram's Romanian voice (WebSocket proxy).

One socket per reply. The page authenticates (first message, see ``admin_panel.voice_ws``), then sends
``{"type": "speak", "text": "..."}``. This route opens Deepgram's streaming text-to-speech (Flux TTS, the only Deepgram
voice that speaks Romanian), sends the text with the numbers, dates and times written in words (that voice does not
normalise them), and passes the audio on as it is generated, so the first sound comes after a fraction of a second
instead of after the whole reply is synthesised. The Deepgram key stays on the server.

Messages to the page:
  {"type": "ready", "sample_rate": 24000}   then binary frames: 16-bit little-endian mono PCM at that rate
  {"type": "done"}                          the reply has been sent completely, the socket closes
  {"type": "error", "code", "message"}      then the socket closes
From the page: ``{"type": "cancel"}`` (or just closing the socket) stops the speech.
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus, WebSocketException

from admin_panel import ro_speech, voice_config
from admin_panel.voice_ws import authenticate, send_error
from utils.logging.setup import get_logger

router = APIRouter()
log = get_logger(__name__)

__all__ = ["router", "AUTH_TIMEOUT_SECONDS"]

AUTH_TIMEOUT_SECONDS = 10.0  # for the auth message, and again for the text that follows it


async def _text_to_speak(ws: WebSocket) -> str | None:
    """The reply to read, from the page's ``speak`` message; None (after telling the page) when it is missing or wrong."""
    try:
        message = await asyncio.wait_for(ws.receive_json(), timeout=AUTH_TIMEOUT_SECONDS)
    except (asyncio.TimeoutError, ValueError, WebSocketDisconnect, RuntimeError):
        await send_error(ws, "bad_request", "the text to speak is missing", 1008)
        return None
    text = message.get("text") if isinstance(message, dict) and message.get("type") == "speak" else None
    if not isinstance(text, str) or not text.strip():
        await send_error(ws, "bad_request", "the text to speak is missing", 1008)
        return None
    if len(text) > voice_config.TTS_MAX_CHARS:
        await send_error(ws, "bad_request", f"text longer than {voice_config.TTS_MAX_CHARS} characters", 1008)
        return None
    return text.strip()


@router.websocket("/api/voice/speak/stream")
async def voice_speak(ws: WebSocket) -> None:
    await ws.accept()
    if not await authenticate(ws, AUTH_TIMEOUT_SECONDS):
        return
    key = voice_config.api_key()
    if key is None or not voice_config.tts_enabled():
        await send_error(ws, "not_configured", "the Deepgram voice is not available on this server")
        return
    text = await _text_to_speak(ws)
    if text is None:
        return

    try:
        upstream = await connect(
            voice_config.ws_speak_url(), additional_headers={"Authorization": f"Token {key}"}, open_timeout=10,
            max_size=2**20,
        )
    except InvalidStatus as exc:
        status = exc.response.status_code
        log.warning("voice_speak: Deepgram refused the connection (HTTP %s)", status)
        if status in (401, 403):
            await send_error(ws, "upstream_auth", "Deepgram rejected the API key")
        else:
            await send_error(ws, "upstream", f"Deepgram refused the connection (HTTP {status})")
        return
    except (OSError, WebSocketException, asyncio.TimeoutError) as exc:
        log.warning("voice_speak: could not reach Deepgram: %s", type(exc).__name__)
        await send_error(ws, "upstream", "could not reach Deepgram")
        return

    async def page_to_upstream() -> None:
        """Ends when the page cancels or goes away."""
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                return
            if message.get("text") is not None:
                try:
                    control = json.loads(message["text"])
                except ValueError:
                    continue
                if isinstance(control, dict) and control.get("type") == "cancel":
                    return

    async def upstream_to_page() -> bool:
        """Audio to the page as it comes. True when Deepgram finished the reply (SpeechMetadata closes a turn)."""
        async for raw in upstream:
            if isinstance(raw, bytes):
                await ws.send_bytes(raw)
                continue
            try:
                kind = json.loads(raw).get("type")
            except ValueError:
                continue
            if kind == "SpeechMetadata":
                return True
        return False

    tasks: list[asyncio.Task] = []
    try:
        await upstream.send(json.dumps({"type": "Speak", "text": ro_speech.normalize(text)}))
        await upstream.send(json.dumps({"type": "Flush"}))
        await ws.send_json({"type": "ready", "sample_rate": voice_config.TTS_SAMPLE_RATE})
        tasks = [asyncio.create_task(page_to_upstream()), asyncio.create_task(upstream_to_page())]
        finished, _ = await asyncio.wait(tasks, timeout=voice_config.MAX_SPEAK_SECONDS, return_when=asyncio.FIRST_COMPLETED)
        if not finished:
            await send_error(ws, "time_limit", "the reply took too long to speak", 1000)
        elif tasks[1] in finished:
            failure = tasks[1].exception() if not tasks[1].cancelled() else None
            if failure is not None and not isinstance(failure, (ConnectionClosed, WebSocketDisconnect, RuntimeError)):
                log.warning("voice_speak: %s", type(failure).__name__)
            if failure is None and tasks[1].result():
                await ws.send_json({"type": "done"})
            else:
                await send_error(ws, "upstream", "the voice connection closed before the reply was complete")
    except (ConnectionClosed, WebSocketDisconnect, RuntimeError):
        pass  # the page or Deepgram went away; the cleanup below is all that is left to do
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await upstream.send(json.dumps({"type": "Close"}))
            await upstream.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass
