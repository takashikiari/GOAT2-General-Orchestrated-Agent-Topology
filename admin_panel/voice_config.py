"""admin_panel.voice_config — where the speech-to-text provider (Deepgram) is and how it is called.

The API key is read from the environment at call time (``DEEPGRAM_API_KEY``, from GOAT's ``.env``, never from the
browser and never logged). Everything else has a default and can be overridden by environment variables, which is also
how the tests point the code at a local fake.
"""
from __future__ import annotations

import os
from urllib.parse import urlencode

__all__ = ["api_key", "http_listen_url", "ws_listen_url", "MODEL", "LANGUAGE", "KEEPALIVE_SECONDS", "MAX_STREAM_SECONDS"]

# Nova-3 supports Romanian ("ro"); Flux (the conversational turn-taking model) does not.
MODEL = os.environ.get("DEEPGRAM_MODEL", "nova-3")
LANGUAGE = os.environ.get("DEEPGRAM_LANGUAGE", "ro")
KEEPALIVE_SECONDS = 4.0            # Deepgram closes a stream after 10 s without audio or a KeepAlive
MAX_STREAM_SECONDS = 30 * 60       # hard cap on one streaming session, whatever the page does


def api_key() -> str | None:
    key = os.environ.get("DEEPGRAM_API_KEY", "").strip()
    return key or None


def http_listen_url() -> str:
    """Pre-recorded transcription (one finished recording)."""
    base = os.environ.get("DEEPGRAM_HTTP_URL", "https://api.deepgram.com/v1/listen")
    return f"{base}?{urlencode({'model': MODEL, 'language': LANGUAGE, 'smart_format': 'true', 'punctuate': 'true'})}"


def ws_listen_url() -> str:
    """Live streaming: 16 kHz mono 16-bit PCM in, interim and final transcripts out, with end-of-speech detection."""
    base = os.environ.get("DEEPGRAM_LISTEN_URL", "wss://api.deepgram.com/v1/listen")
    params = {
        "model": MODEL, "language": LANGUAGE,
        "encoding": "linear16", "sample_rate": "16000", "channels": "1",
        "interim_results": "true",   # required for utterance_end_ms
        "endpointing": "400",        # ms of silence that finalises a segment (speech_final)
        "utterance_end_ms": "1200",  # ms gap after the last word that sends UtteranceEnd
        "vad_events": "true",        # SpeechStarted
        "smart_format": "true", "punctuate": "true",
    }
    return f"{base}?{urlencode(params)}"
