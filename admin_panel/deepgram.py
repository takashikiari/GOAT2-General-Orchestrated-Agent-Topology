"""admin_panel.deepgram — the calls to Deepgram's speech-to-text API that the panel makes on the server."""
from __future__ import annotations

import httpx

from admin_panel import voice_config
from utils.logging.setup import get_logger

log = get_logger(__name__)

__all__ = ["DeepgramError", "transcribe"]


class DeepgramError(Exception):
    """A Deepgram call failed. The message is safe to show: it never contains the API key or the response body."""


async def transcribe(audio: bytes, content_type: str, *, transport: httpx.AsyncBaseTransport | None = None) -> str:
    """Transcribe one finished recording (pre-recorded API). Returns the text, "" when nothing was recognised."""
    key = voice_config.api_key()
    if key is None:
        raise DeepgramError("Deepgram is not configured on the server (DEEPGRAM_API_KEY is missing)")
    headers = {"Authorization": f"Token {key}", "Content-Type": content_type}
    try:
        async with httpx.AsyncClient(timeout=30.0, transport=transport) as client:
            resp = await client.post(voice_config.http_listen_url(), content=audio, headers=headers)
    except httpx.HTTPError as exc:
        log.warning("deepgram: request failed: %s", type(exc).__name__)
        raise DeepgramError("could not reach Deepgram") from exc
    if resp.status_code in (401, 403):
        raise DeepgramError("Deepgram rejected the API key")
    if resp.status_code != 200:
        log.warning("deepgram: HTTP %s", resp.status_code)
        raise DeepgramError(f"Deepgram error (HTTP {resp.status_code})")
    try:
        return (resp.json()["results"]["channels"][0]["alternatives"][0].get("transcript") or "").strip()
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise DeepgramError("unexpected answer from Deepgram") from exc
