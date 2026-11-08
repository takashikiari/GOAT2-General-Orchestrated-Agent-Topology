"""admin_panel.voice_config — where the speech provider (Deepgram: speech-to-text and text-to-speech) is and how it is called.

The API key is read from the environment at call time (``DEEPGRAM_API_KEY``, from GOAT's ``.env``, never from the
browser and never logged). Everything else has a default and can be overridden by environment variables, which is also
how the tests point the code at a local fake.
"""
from __future__ import annotations

import os
from urllib.parse import urlencode

__all__ = [
    "api_key", "http_listen_url", "ws_listen_url", "ws_speak_url", "tts_enabled", "MODEL", "LANGUAGE", "TTS_MODEL",
    "TTS_SAMPLE_RATE", "TTS_MAX_CHARS", "KEEPALIVE_SECONDS", "MAX_STREAM_SECONDS", "MAX_SPEAK_SECONDS",
]

# Nova-3 supports Romanian ("ro"); Flux (the conversational turn-taking model) does not.
MODEL = os.environ.get("DEEPGRAM_MODEL", "nova-3")
LANGUAGE = os.environ.get("DEEPGRAM_LANGUAGE", "ro")
# Flux TTS is the only Deepgram text-to-speech with Romanian (Aura and Aura-2 have none): flux-ioana-ro female, flux-cosmin-ro male.
TTS_MODEL = os.environ.get("DEEPGRAM_TTS_MODEL", "flux-ioana-ro")
TTS_SAMPLE_RATE = 24000            # Hz, 16-bit mono PCM: what the server asks Deepgram for and tells the page
TTS_MAX_CHARS = 1000               # one reply (the page already cuts it to about 700)
MAX_SPEAK_SECONDS = 90             # hard cap on one spoken reply, whatever Deepgram does
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


def ws_speak_url() -> str:
    """Live text-to-speech (Flux TTS): text in, raw 16-bit PCM out as it is generated."""
    base = os.environ.get("DEEPGRAM_SPEAK_URL", "wss://api.deepgram.com/v2/speak")
    return f"{base}?{urlencode({'model': TTS_MODEL, 'encoding': 'linear16', 'sample_rate': str(TTS_SAMPLE_RATE)})}"


def tts_enabled() -> bool:
    """Deepgram voice-back is on when there is a key, unless ``DEEPGRAM_TTS=off`` (the page then uses the browser voice)."""
    return api_key() is not None and os.environ.get("DEEPGRAM_TTS", "on").strip().lower() not in ("off", "0", "false", "no")
