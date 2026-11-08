"""tests.test_voice_speak — /api/voice/speak/stream: GOAT's reply spoken with Flux TTS (WebSocket proxy), against a local fake."""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import threading
import time
from concurrent.futures import CancelledError as FutureCancelled
from http import HTTPStatus
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlparse

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from websockets.asyncio.server import serve

import admin_panel.routes.voice_speak as speak_route
import admin_panel.telegram_auth as auth_module
from admin_panel import voice_config
from admin_panel.server import create_app

_BOT_TOKEN = "123456:test-bot-token"
KEY = "fake-deepgram-key-123"
FRAMES = [b"\x01\x00" * 1200, b"\x02\x00" * 1200, b"\x03\x00" * 700]


def _init_data(age: int = 0) -> str:
    fields = {"user": json.dumps({"id": 42, "first_name": "T"}), "auth_date": str(int(time.time()) - age)}
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", _BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


class FakeFluxTts:
    """A local stand-in for wss://api.deepgram.com/v2/speak, run in its own thread and event loop.
    ``normal`` answers a Flush with three audio frames, Flushed and SpeechMetadata; ``hangup`` closes after the text;
    ``slow`` never finishes the turn."""

    def __init__(self, mode: str = "normal"):
        self.mode = mode
        self.auth = None
        self.path = None
        self.messages: list[dict] = []
        self.port = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server = None

    def start(self) -> None:
        ready = threading.Event()

        def run() -> None:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)

            async def handler(ws):
                self.auth = ws.request.headers.get("Authorization")
                self.path = ws.request.path
                async for raw in ws:
                    msg = json.loads(raw)
                    self.messages.append(msg)
                    if msg.get("type") == "Close":
                        await ws.close()
                        return
                    if msg.get("type") == "Speak" and self.mode == "hangup":
                        await ws.close()
                        return
                    if msg.get("type") == "Flush" and self.mode == "normal":
                        await ws.send(json.dumps({"type": "SpeechStarted", "speech_id": "s1"}))
                        for frame in FRAMES:
                            await ws.send(frame)
                        await ws.send(json.dumps({"type": "Flushed", "speech_id": "s1"}))
                        await ws.send(json.dumps({"type": "SpeechMetadata", "speech_id": "s1"}))

            def process_request(connection, request):
                if request.headers.get("Authorization") != f"Token {KEY}":
                    return connection.respond(HTTPStatus.UNAUTHORIZED, "no\n")

            async def main() -> None:
                self._server = await serve(handler, "127.0.0.1", 0, process_request=process_request)
                self.port = self._server.sockets[0].getsockname()[1]
                ready.set()
                await self._server.serve_forever()

            try:
                self._loop.run_until_complete(main())
            except (asyncio.CancelledError, RuntimeError):
                pass

        threading.Thread(target=run, daemon=True).start()
        assert ready.wait(5)

    def stop(self) -> None:
        if self._loop and self._server:
            with contextlib.suppress(Exception):  # Server.close() is a coroutine: it has to run on the server's own loop
                asyncio.run_coroutine_threadsafe(self._server.close(), self._loop).result(3)


def _wait(cond, seconds=3.0):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def _make_env(monkeypatch, mode="normal", key=KEY):
    monkeypatch.setattr("config.settings.TELEGRAM_BOT_TOKEN", _BOT_TOKEN)
    monkeypatch.setattr(auth_module, "load_admin_chat_id", lambda: "42")
    monkeypatch.setattr(auth_module, "AUTH_WRITE_MAX_AGE_SECONDS", 14400)
    fake = FakeFluxTts(mode)
    fake.start()
    if key:
        monkeypatch.setenv("DEEPGRAM_API_KEY", key)
    else:
        monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    monkeypatch.setenv("DEEPGRAM_SPEAK_URL", f"ws://127.0.0.1:{fake.port}/v2/speak")
    return fake


@pytest.fixture
def env(monkeypatch):
    fake = _make_env(monkeypatch)
    with TestClient(create_app(SimpleNamespace())) as client:
        yield SimpleNamespace(client=client, fake=fake, monkeypatch=monkeypatch)
    fake.stop()


@contextlib.contextmanager
def _session(env, init: str | None = None, text: str | None = "Am găsit 3 erori, 20% din cereri.", auth: bool = True):
    """A WebSocket to the route; the test client cancels the app task when it closes, which can surface as a
    CancelledError on exit: that is teardown, not behaviour, so it is swallowed here."""
    ctx = env.client.websocket_connect("/api/voice/speak/stream")
    session = ctx.__enter__()
    if auth:
        session.send_json({"type": "auth", "initData": _init_data() if init is None else init})
    if text is not None:
        session.send_json({"type": "speak", "text": text})
    try:
        yield session
    finally:
        try:
            ctx.__exit__(None, None, None)
        except (FutureCancelled, asyncio.CancelledError):
            pass


def test_speaks_the_text_streaming_the_audio_back_in_order(env):
    with _session(env) as s:
        assert s.receive_json() == {"type": "ready", "sample_rate": 24000}
        assert [s.receive_bytes() for _ in FRAMES] == FRAMES
        assert s.receive_json() == {"type": "done"}
    # the numbers were written in words before Deepgram got the text, then the turn was flushed
    assert [m["type"] for m in env.fake.messages[:2]] == ["Speak", "Flush"]
    assert env.fake.messages[0]["text"] == "Am găsit trei erori, douăzeci la sută din cereri."


def test_the_key_goes_to_deepgram_only_and_the_voice_is_romanian_pcm(env):
    with _session(env) as s:
        s.receive_json()
    assert env.fake.auth == f"Token {KEY}"
    params = {k: v[0] for k, v in parse_qs(urlparse(env.fake.path).query).items()}
    assert params == {"model": "flux-ioana-ro", "encoding": "linear16", "sample_rate": "24000"}
    assert KEY not in json.dumps(params)


def test_cancel_closes_the_deepgram_side(env):
    env.fake.mode = "slow"
    with _session(env) as s:
        assert s.receive_json()["type"] == "ready"
        s.send_json({"type": "cancel"})
        assert _wait(lambda: any(m.get("type") == "Close" for m in env.fake.messages))


def test_without_valid_auth_nothing_is_forwarded_and_no_deepgram_connection_is_made(env):
    with _session(env, init=_init_data(age=5 * 3600)) as s:  # older than the write limit
        msg = s.receive_json()
        assert msg["type"] == "error" and msg["code"] == "unauthorized"
        with pytest.raises(WebSocketDisconnect):
            s.receive_json()
    assert env.fake.path is None


def test_a_wrong_first_message_is_refused(env):
    with _session(env, auth=False, text=None) as s:
        s.send_json({"type": "hello"})
        assert s.receive_json()["code"] == "unauthorized"
    assert env.fake.path is None


@pytest.mark.parametrize("text", ["", "   ", "a" * 1001])
def test_a_missing_or_too_long_text_is_a_bad_request(env, text):
    with _session(env, text=text) as s:
        assert s.receive_json()["code"] == "bad_request"
    assert env.fake.path is None


def test_a_silent_page_gets_a_bad_request_after_the_timeout(env):
    env.monkeypatch.setattr(speak_route, "AUTH_TIMEOUT_SECONDS", 0.2)
    with _session(env, text=None) as s:
        assert s.receive_json()["code"] == "bad_request"


def test_not_configured_without_a_key_or_when_switched_off(monkeypatch):
    fake = _make_env(monkeypatch, key=None)
    try:
        with TestClient(create_app(SimpleNamespace())) as client:
            env = SimpleNamespace(client=client)
            with _session(env) as s:
                assert s.receive_json()["code"] == "not_configured"
            monkeypatch.setenv("DEEPGRAM_API_KEY", KEY)
            monkeypatch.setenv("DEEPGRAM_TTS", "off")
            with _session(env) as s:
                assert s.receive_json()["code"] == "not_configured"
        assert fake.path is None
    finally:
        fake.stop()


def test_a_rejected_key_is_reported_without_echoing_it(monkeypatch):
    fake = _make_env(monkeypatch, key="another-key-999")
    try:
        with TestClient(create_app(SimpleNamespace())) as client:
            with _session(SimpleNamespace(client=client)) as s:
                msg = s.receive_json()
                assert msg["code"] == "upstream_auth" and "another-key-999" not in json.dumps(msg)
    finally:
        fake.stop()


def test_deepgram_hanging_up_before_the_reply_is_complete_is_an_error(monkeypatch):
    fake = _make_env(monkeypatch, mode="hangup")
    try:
        with TestClient(create_app(SimpleNamespace())) as client:
            with _session(SimpleNamespace(client=client)) as s:
                assert s.receive_json()["type"] == "ready"
                assert s.receive_json()["code"] == "upstream"
    finally:
        fake.stop()


def test_an_unreachable_deepgram_is_an_error(monkeypatch):
    fake = _make_env(monkeypatch)
    fake.stop()
    monkeypatch.setenv("DEEPGRAM_SPEAK_URL", "ws://127.0.0.1:1/v2/speak")
    with TestClient(create_app(SimpleNamespace())) as client:
        with _session(SimpleNamespace(client=client)) as s:
            assert s.receive_json()["code"] == "upstream"


def test_the_voice_is_romanian_and_can_be_switched_off():
    assert voice_config.TTS_MODEL.endswith("-ro")
