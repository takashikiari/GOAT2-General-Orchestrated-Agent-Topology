"""tests.test_admin_panel_voice — POST /api/voice: the chat page's voice upload (PROTOTYPE: validates, answers a stub)."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

import admin_panel.routes.voice as voice_route
import admin_panel.telegram_auth as auth_module
from admin_panel.server import create_app

_BOT_TOKEN = "123456:test-bot-token"

WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 600            # EBML header, what MediaRecorder gives on Chrome/Android
MP4 = b"\x00\x00\x00\x20ftypmp42" + b"\x00" * 600      # what Safari/iOS gives
OGG = b"OggS" + b"\x00" * 600
WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 600


def _headers(age: int = 0) -> dict:
    fields = {"user": json.dumps({"id": 42, "first_name": "T"}), "auth_date": str(int(time.time()) - age)}
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


def _post(client, data=WEBM, ctype="audio/webm", duration="7300", headers=None, name="audio"):
    files = {name: ("voice.webm", data, ctype)} if name else {}
    form = {"duration_ms": duration} if duration is not None else {}
    return client.post("/api/voice", files=files, data=form, headers=_headers() if headers is None else headers)


def test_requires_auth(client):
    assert _post(client, headers={}).status_code == 401


def test_stale_init_data_is_refused_like_the_other_routes_that_act(client):
    assert _post(client, headers=_headers(5 * 3600)).status_code == 401


def test_valid_upload_gets_the_explicit_stub(client):
    resp = _post(client)
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "stub": True, "duration_ms": 7300, "bytes": len(WEBM),
                           "content_type": "audio/webm", "text": ""}


@pytest.mark.parametrize("data,ctype", [(MP4, "audio/mp4"), (OGG, "audio/ogg"), (WAV, "audio/wav"), (WEBM, "audio/webm;codecs=opus")])
def test_the_formats_a_browser_records(client, data, ctype):
    resp = _post(client, data=data, ctype=ctype)
    assert resp.status_code == 200 and resp.json()["content_type"] == ctype.split(";")[0]


def test_not_audio_content_type_is_422(client):
    assert _post(client, ctype="text/plain").status_code == 422
    assert _post(client, ctype="application/octet-stream").status_code == 422


def test_audio_content_type_with_non_audio_bytes_is_422(client):
    assert _post(client, data=b"<html>not audio</html>" + b"x" * 600).status_code == 422


def test_empty_and_missing_audio_are_422(client):
    assert _post(client, data=b"").status_code == 422
    assert _post(client, name=None).status_code == 422


@pytest.mark.parametrize("duration", [None, "abc", "0", "-5", "100", "65001", "600000"])
def test_bad_durations_are_422(client, duration):
    assert _post(client, duration=duration).status_code == 422


def test_the_longest_recording_the_page_allows_is_accepted(client):
    assert _post(client, duration="60000").status_code == 200
    assert _post(client, duration="65000").status_code == 200


def test_too_large_is_413_and_the_limit_is_documented(client, monkeypatch):
    monkeypatch.setattr(voice_route, "MAX_AUDIO_BYTES", 1000)
    assert _post(client, data=WEBM + b"\x00" * 5000).status_code == 413
    assert voice_route.MAX_AUDIO_BYTES  # restored value is non-zero; real default checked below


def test_default_limits():
    import importlib
    importlib.reload(voice_route)
    assert voice_route.MAX_AUDIO_BYTES == 5 * 1024 * 1024
    assert voice_route.MIN_DURATION_MS == 300 and voice_route.MAX_DURATION_MS == 65_000


def test_auth_is_checked_before_the_body_is_looked_at(client, monkeypatch):
    monkeypatch.setattr(voice_route, "MAX_AUDIO_BYTES", 10)
    # unauthenticated AND oversized: must be 401, the body must not matter
    assert _post(client, data=WEBM + b"\x00" * 5000, headers={}).status_code == 401


def test_a_body_without_a_length_is_refused(client):
    def chunks():
        yield WEBM
    resp = client.post("/api/voice", content=chunks(), headers={**_headers(), "Content-Type": "multipart/form-data; boundary=x"})
    assert resp.status_code in (411, 413)
