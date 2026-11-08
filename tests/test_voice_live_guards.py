"""tests.test_voice_live_guards — /api/voice/live: who may talk, what is not set up, and the limits."""
from __future__ import annotations

import time

import pytest
from starlette.websockets import WebSocketDisconnect

import admin_panel.routes.voice_live as live_route
import admin_panel.voice_live.session as session_module
from admin_panel.voice_live import providers
from admin_panel.voice_live.fake import FakeProvider
from voice_live_helpers import _init_data, _live, _read, _until, env  # noqa: F401


def test_silence_ends_the_conversation(env):
    env.monkeypatch.setattr("admin_panel.voice_config.LIVE_IDLE_SECONDS", 0.2)
    with _live(env) as s:
        err = _until(s, lambda m: m["type"] == "error")[-1]
        assert err["code"] == "idle"


def test_a_frame_that_is_too_large_is_refused(env):
    with _live(env) as s:
        _until(s, lambda m: m["type"] == "state")
        s.send_bytes(b"\x00" * (session_module.MAX_FRAME_BYTES + 1))
        assert _until(s, lambda m: m["type"] == "error")[-1]["code"] == "frame_too_large"


def test_close_from_the_page_ends_it_and_closes_the_provider(env):
    with _live(env) as s:
        _until(s, lambda m: m["type"] == "state")
        s.send_json({"type": "close"})
        assert _until(s, lambda m: m["type"] == "closed")
    deadline = time.time() + 2
    while not env.made[0].closed and time.time() < deadline:
        time.sleep(0.02)
    assert env.made[0].closed


def test_without_valid_auth_nothing_is_opened(env):
    with _live(env, init=_init_data(age=5 * 3600)) as s:
        msg = _read(s)
        assert msg["type"] == "error" and msg["code"] == "unauthorized"
        with pytest.raises(WebSocketDisconnect):
            s.receive_json()
    assert env.made == []


def test_not_configured_without_a_provider_an_unknown_one_or_gemini_without_its_key(env):
    env.monkeypatch.delenv("VOICE_LIVE_PROVIDER")
    with _live(env) as s:
        assert _read(s)["code"] == "not_configured"
    env.monkeypatch.setenv("VOICE_LIVE_PROVIDER", "nonsense")
    with _live(env) as s:
        assert _read(s)["code"] == "not_configured"
    env.monkeypatch.setenv("VOICE_LIVE_PROVIDER", "gemini")
    env.monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with _live(env) as s:
        assert _read(s)["code"] == "not_configured"


def test_without_a_chat_service_the_live_voice_says_so(env):
    env.registry.chat_service = None
    with _live(env) as s:
        assert _read(s)["code"] == "chat_unavailable"


def test_a_provider_that_cannot_connect_is_an_error_for_the_page(env):
    class Refusing(FakeProvider):
        async def connect(self, config, history):
            raise RuntimeError("no network")

    env.monkeypatch.setitem(providers.PROVIDERS, "fake", lambda: Refusing())
    with _live(env) as s:
        assert _read(s)["code"] == "upstream"


def test_the_route_module_has_its_own_auth_timeout(env):
    env.monkeypatch.setattr(live_route, "AUTH_TIMEOUT_SECONDS", 0.2)
    with _live(env, auth=False) as s:
        assert _read(s)["code"] == "unauthorized"
