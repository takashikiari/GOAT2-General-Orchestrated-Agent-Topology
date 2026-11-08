"""tests.test_voice_stream — /api/voice/stream: the live speech-to-text WebSocket proxy, against a local fake Deepgram."""
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

import admin_panel.routes.voice_stream as stream_route
import admin_panel.telegram_auth as auth_module
from admin_panel import voice_config
from admin_panel.server import create_app

_BOT_TOKEN = "123456:test-bot-token"
KEY = "fake-deepgram-key-123"


def _init_data(age: int = 0) -> str:
    fields = {"user": json.dumps({"id": 42, "first_name": "T"}), "auth_date": str(int(time.time()) - age)}
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", _BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


class FakeDeepgram:
    """A local stand-in for wss://api.deepgram.com/v1/listen, run in its own thread and event loop."""

    def __init__(self, mode: str = "normal"):
        self.mode = mode
        self.auth = None
        self.path = None
        self.audio = bytearray()
        self.texts: list[str] = []
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
                async for msg in ws:
                    if isinstance(msg, bytes):
                        first = not self.audio
                        self.audio += msg
                        if self.mode == "hangup":
                            await ws.close()
                            return
                        if first:
                            for payload in (
                                {"type": "SpeechStarted", "timestamp": 0.1},
                                {"type": "Results", "is_final": False, "speech_final": False,
                                 "channel": {"alternatives": [{"transcript": "salut ce"}]}},
                                {"type": "Results", "is_final": True, "speech_final": True,
                                 "channel": {"alternatives": [{"transcript": "salut, ce faci?"}]}},
                                {"type": "Results", "is_final": True, "speech_final": False,
                                 "channel": {"alternatives": [{"transcript": ""}]}},
                                {"type": "Metadata", "request_id": "x"},
                                {"type": "UtteranceEnd", "last_word_end": 1.2},
                            ):
                                await ws.send(json.dumps(payload))
                    else:
                        self.texts.append(msg)
                        if json.loads(msg).get("type") == "CloseStream":
                            await ws.close()
                            return

            def process_request(connection, request):
                if request.headers.get("Authorization") != f"Token {KEY}":
                    return connection.respond(HTTPStatus.UNAUTHORIZED, "no\n")

            async def main() -> None:
                self._server = await serve(handler, "127.0.0.1", 0, process_request=process_request)
                self.port = self._server.sockets[0].getsockname()[1]
                ready.set()
                try:
                    await self._server.serve_forever()
                finally:
                    await self._server.wait_closed()  # the close task has to finish before this loop goes away

            try:
                self._loop.run_until_complete(main())
            except (asyncio.CancelledError, RuntimeError):
                pass

        threading.Thread(target=run, daemon=True).start()
        assert ready.wait(5)

    def stop(self) -> None:
        if self._loop and self._server:
            self._loop.call_soon_threadsafe(self._server.close)  # serve_forever() then ends, and the thread with it

            with contextlib.suppress(Exception):
                asyncio.run_coroutine_threadsafe(shut(), self._loop).result(3)


def _wait(cond, seconds=3.0):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr("config.settings.TELEGRAM_BOT_TOKEN", _BOT_TOKEN)
    monkeypatch.setattr(auth_module, "load_admin_chat_id", lambda: "42")
    monkeypatch.setattr(auth_module, "AUTH_WRITE_MAX_AGE_SECONDS", 14400)
    fake = FakeDeepgram()
    fake.start()
    monkeypatch.setenv("DEEPGRAM_API_KEY", KEY)
    monkeypatch.setenv("DEEPGRAM_LISTEN_URL", f"ws://127.0.0.1:{fake.port}/v1/listen")
    with TestClient(create_app(SimpleNamespace())) as client:
        yield SimpleNamespace(client=client, fake=fake, monkeypatch=monkeypatch)
    fake.stop()


@contextlib.contextmanager
def _session(env, auth: bool = True, init: str | None = None):
    """A WebSocket to the route; the test client cancels the app task when it closes, which can surface as a
    CancelledError on exit: that is teardown, not behaviour, so it is swallowed here."""
    ctx = env.client.websocket_connect("/api/voice/stream")
    session = ctx.__enter__()
    if auth:
        session.send_json({"type": "auth", "initData": _init_data() if init is None else init})
    try:
        yield session
    finally:
        try:
            ctx.__exit__(None, None, None)
        except (FutureCancelled, asyncio.CancelledError):
            pass


def test_streams_audio_to_deepgram_and_maps_what_comes_back(env):
    with _session(env, env) as s:
        assert s.receive_json() == {"type": "ready"}
        s.send_bytes(b"\x01\x02" * 1600)
        events = [s.receive_json() for _ in range(4)]
        assert events == [
            {"type": "speech_started"},
            {"type": "transcript", "text": "salut ce", "final": False, "speech_final": False},
            {"type": "transcript", "text": "salut, ce faci?", "final": True, "speech_final": True},
            {"type": "utterance_end"},
        ]  # the empty final result and the metadata message are not passed on
        assert bytes(env.fake.audio) == b"\x01\x02" * 1600


def test_the_key_goes_to_deepgram_only_and_the_parameters_are_the_ones_we_need(env):
    with _session(env, env) as s:
        assert s.receive_json() == {"type": "ready"}
        s.send_bytes(b"\x00" * 640)
        assert _wait(lambda: env.fake.path is not None)
    assert env.fake.auth == f"Token {KEY}"
    params = {k: v[0] for k, v in parse_qs(urlparse(env.fake.path).query).items()}
    assert params["model"] == "nova-3" and params["language"] == "ro"
    assert params["encoding"] == "linear16" and params["sample_rate"] == "16000" and params["channels"] == "1"
    assert params["interim_results"] == "true" and params["vad_events"] == "true"
    assert int(params["endpointing"]) >= 300 and int(params["utterance_end_ms"]) >= 1000  # UtteranceEnd needs >= 1000
    assert KEY not in json.dumps(params)


def test_a_client_close_ends_the_deepgram_stream_cleanly(env):
    with _session(env, env) as s:
        assert s.receive_json() == {"type": "ready"}
        s.send_bytes(b"\x00" * 640)
        s.send_json({"type": "close"})
        assert _wait(lambda: any('"CloseStream"' in t for t in env.fake.texts))


def test_without_valid_auth_nothing_is_forwarded_and_no_deepgram_connection_is_made(env):
    with _session(env, env, init=_init_data(age=5 * 3600)) as s:  # older than the write limit
        msg = s.receive_json()
        assert msg["type"] == "error" and msg["code"] == "unauthorized"
        with pytest.raises(WebSocketDisconnect):
            s.receive_json()
    assert env.fake.path is None


def test_a_wrong_first_message_is_refused(env):
    with _session(env, auth=False) as s:
        s.send_json({"type": "hello"})
        assert s.receive_json()["code"] == "unauthorized"


def test_no_first_message_in_time_is_refused(env):
    env.monkeypatch.setattr(stream_route, "AUTH_TIMEOUT_SECONDS", 0.2)
    with _session(env, auth=False) as s:
        assert s.receive_json()["code"] == "unauthorized"


def test_a_missing_key_is_said_plainly(env):
    env.monkeypatch.delenv("DEEPGRAM_API_KEY")
    with _session(env, env) as s:
        msg = s.receive_json()
        assert msg["code"] == "not_configured" and "DEEPGRAM_API_KEY" in msg["message"]
    assert env.fake.path is None


def test_a_rejected_key_is_reported_without_echoing_it(env):
    env.monkeypatch.setenv("DEEPGRAM_API_KEY", "some-other-key")
    with _session(env, env) as s:
        msg = s.receive_json()
        assert msg["code"] == "upstream_auth" and "some-other-key" not in json.dumps(msg)


def test_an_unreachable_deepgram_is_an_upstream_error(env):
    env.fake.stop()
    env.monkeypatch.setenv("DEEPGRAM_LISTEN_URL", "ws://127.0.0.1:1/v1/listen")
    with _session(env, env) as s:
        assert s.receive_json()["code"] == "upstream"


def test_deepgram_hanging_up_mid_stream_is_reported(env):
    env.fake.mode = "hangup"
    with _session(env, env) as s:
        assert s.receive_json() == {"type": "ready"}
        s.send_bytes(b"\x00" * 640)
        assert s.receive_json()["code"] == "upstream"


def test_silence_is_kept_alive_with_text_frames(env):
    env.monkeypatch.setattr(voice_config, "KEEPALIVE_SECONDS", 0.2)
    with _session(env, env) as s:
        assert s.receive_json() == {"type": "ready"}
        assert _wait(lambda: any('"KeepAlive"' in t for t in env.fake.texts), seconds=3)


def test_an_oversized_audio_frame_is_refused(env):
    with _session(env, env) as s:
        assert s.receive_json() == {"type": "ready"}
        s.send_bytes(b"\x00" * (stream_route.MAX_FRAME_BYTES + 1))
        assert s.receive_json()["code"] == "frame_too_large"
