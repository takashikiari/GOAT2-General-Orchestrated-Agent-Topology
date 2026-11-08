"""tests.test_deepgram_http — admin_panel.deepgram.transcribe (pre-recorded) and the push-to-talk route that uses it."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode

import httpx
import pytest
from fastapi.testclient import TestClient

import admin_panel.deepgram as deepgram
import admin_panel.routes.voice as voice_route
import admin_panel.telegram_auth as auth_module
from admin_panel.server import create_app

_BOT_TOKEN = "123456:test-bot-token"
WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 600


def _answer(text="salut"):
    return {"results": {"channels": [{"alternatives": [{"transcript": text, "confidence": 0.99}]}]}}


def _transport(handler):
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_transcribe_calls_deepgram_with_the_key_and_the_audio(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "k-123")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(auth=request.headers["Authorization"], ctype=request.headers["Content-Type"], body=request.content,
                    params={k: v[0] for k, v in parse_qs(request.url.query.decode()).items()})
        return httpx.Response(200, json=_answer("  verifică logurile  "))

    text = await deepgram.transcribe(WEBM, "audio/webm", transport=_transport(handler))
    assert text == "verifică logurile"
    assert seen["auth"] == "Token k-123" and seen["ctype"] == "audio/webm" and seen["body"] == WEBM
    assert seen["params"]["model"] == "nova-3" and seen["params"]["language"] == "ro"


@pytest.mark.asyncio
async def test_transcribe_errors_never_contain_the_key_or_the_body(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "k-secret-999")
    for status, expected in ((401, "rejected the API key"), (403, "rejected the API key"), (500, "HTTP 500"), (429, "HTTP 429")):
        with pytest.raises(deepgram.DeepgramError) as info:
            await deepgram.transcribe(WEBM, "audio/webm", transport=_transport(lambda r, s=status: httpx.Response(s, text="k-secret-999 echoed")))
        assert expected in str(info.value) and "k-secret-999" not in str(info.value)


@pytest.mark.asyncio
async def test_transcribe_handles_a_network_failure_an_odd_answer_and_no_key(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "k")

    def boom(request):
        raise httpx.ConnectError("down")

    with pytest.raises(deepgram.DeepgramError, match="could not reach"):
        await deepgram.transcribe(WEBM, "audio/webm", transport=_transport(boom))
    with pytest.raises(deepgram.DeepgramError, match="unexpected answer"):
        await deepgram.transcribe(WEBM, "audio/webm", transport=_transport(lambda r: httpx.Response(200, json={"nope": 1})))
    assert await deepgram.transcribe(WEBM, "audio/webm", transport=_transport(lambda r: httpx.Response(200, json=_answer("")))) == ""
    monkeypatch.delenv("DEEPGRAM_API_KEY")
    with pytest.raises(deepgram.DeepgramError, match="DEEPGRAM_API_KEY"):
        await deepgram.transcribe(WEBM, "audio/webm")


def _headers() -> dict:
    fields = {"user": json.dumps({"id": 42, "first_name": "T"}), "auth_date": str(int(time.time()))}
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", _BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return {"X-Telegram-Init-Data": urlencode(fields)}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("config.settings.TELEGRAM_BOT_TOKEN", _BOT_TOKEN)
    monkeypatch.setattr(auth_module, "load_admin_chat_id", lambda: "42")
    monkeypatch.setattr(auth_module, "AUTH_WRITE_MAX_AGE_SECONDS", 14400)
    with TestClient(create_app(SimpleNamespace())) as c:
        yield c


def _post(client):
    return client.post("/api/voice", files={"audio": ("v.webm", WEBM, "audio/webm")}, data={"duration_ms": "2500"}, headers=_headers())


def test_the_route_stays_a_stub_when_no_key_is_configured(client, monkeypatch):
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    body = _post(client).json()
    assert body["stub"] is True and body["text"] == ""


def test_the_route_returns_the_transcript_when_a_key_is_configured(client, monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "k")
    seen = {}

    async def fake(audio, content_type, **kw):
        seen.update(audio=audio, ctype=content_type)
        return "verifică logurile"

    monkeypatch.setattr(voice_route.deepgram, "transcribe", fake)
    body = _post(client).json()
    assert body == {"ok": True, "stub": False, "duration_ms": 2500, "bytes": len(WEBM), "content_type": "audio/webm", "text": "verifică logurile"}
    assert seen == {"audio": WEBM, "ctype": "audio/webm"}


def test_a_deepgram_failure_is_a_502_with_a_safe_message(client, monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "k-secret")

    async def fake(audio, content_type, **kw):
        raise deepgram.DeepgramError("Deepgram rejected the API key")

    monkeypatch.setattr(voice_route.deepgram, "transcribe", fake)
    resp = _post(client)
    assert resp.status_code == 502 and "rejected the API key" in resp.json()["detail"] and "k-secret" not in resp.text
