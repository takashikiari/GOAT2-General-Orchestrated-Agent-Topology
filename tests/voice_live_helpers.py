"""tests.voice_live_helpers — what the /api/voice/live tests share: the app with a real ChatService and a fake GOAT, the
scripted fake provider, and a few helpers to read the WebSocket."""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import time
from collections import defaultdict
from concurrent.futures import CancelledError as FutureCancelled
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

import admin_panel.telegram_auth as auth_module
import admin_panel.voice_live.session as session_module
from admin_panel.server import create_app
from admin_panel.voice_live import providers
from admin_panel.voice_live.fake import FakeProvider
from orchestrator import turn_events
from orchestrator.chat_service import ChatService

_BOT_TOKEN = "123456:test-bot-token"
CHAT = "42"
FRAME = b"\x01\x00" * 1600  # 0.1 s of 16 kHz audio; the fake hears an utterance every two frames


def _init_data(age: int = 0) -> str:
    fields = {"user": json.dumps({"id": 42, "first_name": "T"}), "auth_date": str(int(time.time()) - age)}
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", _BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


class WorkingMemory:
    def __init__(self, messages=None, fail=False):
        self.messages, self.fail = messages or [], fail

    async def get_messages(self, chat_id):
        if self.fail:
            raise RuntimeError("redis down")
        return list(self.messages)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr("config.settings.TELEGRAM_BOT_TOKEN", _BOT_TOKEN)
    monkeypatch.setattr(auth_module, "load_admin_chat_id", lambda: CHAT)
    monkeypatch.setattr(auth_module, "AUTH_WRITE_MAX_AGE_SECONDS", 14400)
    monkeypatch.setenv("VOICE_LIVE_PROVIDER", "fake")
    monkeypatch.setattr(session_module, "WATCHDOG_TICK", 0.05)
    asked: list[str] = []
    state = SimpleNamespace(reply="Totul e în regulă, 3 erori.", delay=0.05, fail=False)

    async def fake_run(text, chat_id, *, on_context_assembled=None):
        asked.append(text)
        turn_events.emit({"type": "tool_start", "id": "1", "name": "get_recent_logs", "args": "{}"})
        await asyncio.sleep(state.delay)
        turn_events.emit({"type": "tool_end", "id": "1", "name": "get_recent_logs", "result": "linie"})
        if state.fail:
            raise RuntimeError("boom")
        return state.reply

    service = ChatService(run=fake_run, locks=defaultdict(asyncio.Lock), chat_id=lambda: CHAT)
    registry = SimpleNamespace(chat_service=service, working_memory=WorkingMemory())
    providers_made: list[FakeProvider] = []

    def make(**kw):
        def factory():
            p = FakeProvider(pace=0.0, tone_seconds=0.1, trigger_bytes=6400, **kw)
            providers_made.append(p)
            return p
        return factory

    monkeypatch.setitem(providers.PROVIDERS, "fake", make())
    with TestClient(create_app(registry)) as client:
        yield SimpleNamespace(client=client, registry=registry, asked=asked, run=state, made=providers_made,
                              monkeypatch=monkeypatch, make=make, service=service)


@contextlib.contextmanager
def _live(env, init=None, auth=True):
    """A WebSocket to the route (the test client's teardown can surface a CancelledError: swallowed, it is not behaviour)."""
    ctx = env.client.websocket_connect("/api/voice/live")
    s = ctx.__enter__()
    if auth:
        s.send_json({"type": "auth", "initData": _init_data() if init is None else init})
    try:
        yield s
    finally:
        try:
            ctx.__exit__(None, None, None)
        except (FutureCancelled, asyncio.CancelledError):
            pass


def _read(s):
    """One message from the server: a JSON dict, or {"audio": n} for a binary frame."""
    msg = s.receive()
    if msg["type"] == "websocket.close":
        return {"type": "closed", "code": msg.get("code")}
    if msg.get("bytes") is not None:
        return {"type": "audio", "bytes": len(msg["bytes"])}
    return json.loads(msg["text"])


def _until(s, predicate, limit=200):
    seen = []
    for _ in range(limit):
        m = _read(s)
        seen.append(m)
        if predicate(m):
            return seen
    raise AssertionError(f"never reached: {seen[-8:]}")


def _types(seen):
    return [m["type"] for m in seen]
