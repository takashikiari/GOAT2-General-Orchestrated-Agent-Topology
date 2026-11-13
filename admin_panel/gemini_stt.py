"""admin_panel.gemini_stt — a recorded voice message transcribed by Gemini: the ear only (Laris, 2026-11-13).

Gemini is asked for a verbatim Romanian transcript and nothing else: it never answers, never summarises, and no audio
output is ever requested. The clip is first converted to 16 kHz mono WAV with ffmpeg, so whatever the browser recorded
(webm/opus on Chrome, mp4/aac on iOS) reaches Gemini in a format it reads. One ``generateContent`` call per message; the
key stays on the server and is never logged.
"""
from __future__ import annotations

import asyncio
import base64

import httpx

from admin_panel import voice_config
from utils.logging.setup import get_logger

log = get_logger(__name__)

__all__ = ["SttError", "INSTRUCTION", "to_wav", "transcribe"]

INSTRUCTION = (
    "Transcribe this Romanian speech exactly, word for word, in Romanian. Output ONLY the transcript: no answer, no "
    "summary, no translation, no comments, no quotes. Keep the speaker's own words, including hesitations that carry "
    "meaning; add only sentence punctuation. If there is no intelligible speech, output nothing."
)
_FFMPEG_TIMEOUT = 20.0


class SttError(Exception):
    """Transcription failed. The message is safe to show: it never contains the API key or a response body."""


async def to_wav(audio: bytes) -> bytes:
    """The clip as 16 kHz mono 16-bit WAV (ffmpeg, stdin → stdout)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-ac", "1", "-ar", "16000",
            "-f", "wav", "pipe:1",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise SttError("ffmpeg is not installed on the server") from exc
    try:
        out, err = await asyncio.wait_for(proc.communicate(audio), _FFMPEG_TIMEOUT)
    except asyncio.TimeoutError as exc:
        proc.kill()
        raise SttError("the recording could not be converted in time") from exc
    if proc.returncode != 0 or not out:
        log.warning("gemini_stt: ffmpeg failed rc=%s: %s", proc.returncode, (err or b"")[:200].decode(errors="replace"))
        raise SttError("the recording could not be read")
    return out


async def transcribe(audio: bytes, *, transport: httpx.AsyncBaseTransport | None = None) -> str:
    """Verbatim transcript of one recorded clip; "" when no speech was recognised."""
    key = voice_config.gemini_key()
    if key is None:
        raise SttError("Gemini is not configured on the server (GEMINI_API_KEY is missing)")
    wav = await to_wav(audio)
    body = {
        "contents": [{"role": "user", "parts": [
            {"text": INSTRUCTION},
            {"inline_data": {"mime_type": "audio/wav", "data": base64.b64encode(wav).decode()}},
        ]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "text/plain"},
    }
    url = f"{voice_config.gemini_rest_url()}/models/{voice_config.gemini_stt_model()}:generateContent"
    try:
        async with httpx.AsyncClient(timeout=30.0, transport=transport) as client:
            resp = await client.post(url, json=body, headers={"x-goog-api-key": key})
    except httpx.HTTPError as exc:
        log.warning("gemini_stt: request failed: %s", type(exc).__name__)
        raise SttError("could not reach Gemini") from exc
    if resp.status_code in (401, 403):
        raise SttError("Gemini rejected the API key")
    if resp.status_code != 200:
        log.warning("gemini_stt: HTTP %s", resp.status_code)
        raise SttError(f"Gemini error (HTTP {resp.status_code})")
    try:
        parts = resp.json()["candidates"][0]["content"].get("parts") or []
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise SttError("unexpected answer from Gemini") from exc
    return " ".join(str(p.get("text", "")).strip() for p in parts if isinstance(p, dict)).strip()
