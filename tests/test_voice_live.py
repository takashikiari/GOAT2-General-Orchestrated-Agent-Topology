"""tests.test_voice_live — /api/voice/live: the speech-to-speech conversation, against the scripted fake provider and a
real ChatService with a fake GOAT."""
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
from starlette.websockets import WebSocketDisconnect

import admin_panel.routes.voice_live as live_route
import admin_panel.telegram_auth as auth_module
import admin_panel.voice_live.session as session_module
from admin_panel.server import create_app
from admin_panel.voice_live import providers
from admin_panel.voice_live.fake import FakeProvider
from admin_panel.voice_live.provider import ProviderError, ToolCall, ToolCancel
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


def test_a_spoken_request_goes_to_goat_and_the_answer_is_spoken_back(env):
    with _live(env) as s:
        assert _read(s) == {"type": "ready", "in_rate": 16000, "out_rate": 24000}
        assert _read(s) == {"type": "state", "state": "listening"}
        s.send_bytes(FRAME)
        s.send_bytes(FRAME)  # 6400 bytes: the fake "hears" the first utterance
        seen = _until(s, lambda m: m == {"type": "tool", "name": "get_recent_logs", "status": "done"})
        kinds = _types(seen)
        assert {"type": "user_text", "text": "verifică logurile", "final": False} in seen
        assert {"type": "user_text", "text": "verifică logurile din ultima oră", "final": True} in seen
        assert {"type": "assistant_text", "text": "O secundă, verific."} in seen  # the filler, while GOAT works
        assert "audio" in kinds and {"type": "tool", "name": "get_recent_logs", "status": "running"} in seen
        # the answer comes back and is spoken after the model is idle
        rest = _until(s, lambda m: m["type"] == "assistant_text" and m["text"].startswith("Am aflat"))
        assert rest[-1]["text"] == "Am aflat: Totul e în regulă, 3 erori."
        _until(s, lambda m: m == {"type": "state", "state": "listening"})
    provider = env.made[0]
    assert env.asked == ["verifică logurile din ultima oră"]
    assert provider.tool_results == [("call-1", "ask_goat", "Totul e în regulă, 3 erori.", "WHEN_IDLE")]


def test_small_talk_is_answered_by_the_voice_model_alone(env):
    env.monkeypatch.setitem(providers.PROVIDERS, "fake", env.make(script=(("chat", "mulțumesc"),)))
    with _live(env) as s:
        _until(s, lambda m: m["type"] == "state")
        s.send_bytes(FRAME)
        s.send_bytes(FRAME)
        spoken = _until(s, lambda m: m["type"] == "assistant_text")[-1]
        assert spoken["text"] == "Cu plăcere. Ai spus: mulțumesc"
        _until(s, lambda m: m == {"type": "state", "state": "listening"})
    assert env.asked == [] and env.made[0].tool_results == []


def test_a_tap_cuts_the_model_off(env):
    env.monkeypatch.setitem(providers.PROVIDERS, "fake", env.make(script=(("chat", "salut"),)))
    with _live(env) as s:
        _until(s, lambda m: m["type"] == "state")
        s.send_bytes(FRAME)
        s.send_bytes(FRAME)
        _until(s, lambda m: m["type"] == "audio")
        s.send_json({"type": "interrupt"})
        _until(s, lambda m: m["type"] == "interrupted")
    assert env.made[0].interrupts == 1


def test_a_cancelled_tool_call_is_answered_silently_and_goat_still_finishes(env):
    class Cancelling(FakeProvider):
        async def _hear(self, kind, text):
            self._queue.put_nowait(ToolCall(id="c9", name="ask_goat", args={"request": text}))
            self._queue.put_nowait(ToolCancel(("c9",)))

    env.monkeypatch.setitem(providers.PROVIDERS, "fake", lambda: env.made.append(Cancelling((("tool", "verifică"),), pace=0.0, trigger_bytes=6400)) or env.made[-1])
    with _live(env) as s:
        _until(s, lambda m: m["type"] == "state")
        s.send_bytes(FRAME)
        s.send_bytes(FRAME)
        _until(s, lambda m: m == {"type": "tool", "name": "get_recent_logs", "status": "done"})
        _until(s, lambda m: m == {"type": "state", "state": "listening"})
    assert env.made[0].tool_results == [("c9", "ask_goat", "Totul e în regulă, 3 erori.", "SILENT")]


def test_a_second_request_while_goat_is_busy_is_told_to_wait(env):
    env.run.delay = 0.5
    env.monkeypatch.setitem(providers.PROVIDERS, "fake", env.make(script=(("tool", "prima"), ("tool", "a doua"))))
    with _live(env) as s:
        _until(s, lambda m: m["type"] == "state")
        for _ in range(4):
            s.send_bytes(FRAME)
        _until(s, lambda m: m == {"type": "tool", "name": "get_recent_logs", "status": "done"})
        _until(s, lambda m: m["type"] == "assistant_text" and m["text"].startswith("Am aflat"))
    results = {r[0]: r[2] for r in env.made[0].tool_results}
    assert results["call-2"].startswith("GOAT is still working")
    assert results["call-1"] == "Totul e în regulă, 3 erori."
    assert env.asked == ["prima"]


def test_goat_failing_or_an_unknown_tool_is_reported_to_the_model_in_words(env):
    env.run.fail = True
    with _live(env) as s:
        _until(s, lambda m: m["type"] == "state")
        s.send_bytes(FRAME)
        s.send_bytes(FRAME)
        _until(s, lambda m: m["type"] == "assistant_text" and m["text"].startswith("Am aflat"))
    assert env.made[0].tool_results[0][2] == "GOAT could not finish that request."


def test_the_session_starts_from_the_recent_chat_without_agent_digests_or_tool_evidence(env):
    env.registry.working_memory = WorkingMemory([
        {"role": "user", "content": "ce ai făcut azi?", "timestamp": 1},
        {"role": "assistant", "content": "[Tool calls]\ncalled get_recent_logs({})\n\nAm verificat logurile.", "timestamp": 2},
        {"role": "user", "content": "x" * 800, "timestamp": 3},
    ])
    with _live(env) as s:
        _until(s, lambda m: m["type"] == "state")
    items = env.made[0].history
    assert [(i.role, i.text) for i in items[:2]] == [("user", "ce ai făcut azi?"), ("model", "Am verificat logurile.")]
    assert items[2].role == "user" and len(items[2].text) == 601 and items[2].text.endswith("…")  # cut at 600 characters
    assert len(items) == 3
    assert env.made[0].config.tools[0]["name"] == "ask_goat" and "Romanian" in env.made[0].config.system_prompt


def test_the_conversation_still_starts_when_the_history_cannot_be_read(env):
    env.registry.working_memory = WorkingMemory(fail=True)
    with _live(env) as s:
        assert _read(s)["type"] == "ready"
    assert env.made[0].history == []


def test_the_provider_going_away_is_passed_on_and_an_error_ends_the_session(env):
    env.monkeypatch.setitem(providers.PROVIDERS, "fake", env.make(script=(("chat", "salut"),), go_away_after=1))
    with _live(env) as s:
        _until(s, lambda m: m["type"] == "state")
        s.send_bytes(FRAME)
        s.send_bytes(FRAME)
        notice = _until(s, lambda m: m["type"] == "notice")[-1]
        assert notice == {"type": "notice", "code": "go_away", "seconds": 5.0}

    class Failing(FakeProvider):
        async def send_audio(self, pcm):
            self._queue.put_nowait(ProviderError("quota", "quota exceeded"))

    env.monkeypatch.setitem(providers.PROVIDERS, "fake", lambda: Failing())
    with _live(env) as s:
        _until(s, lambda m: m["type"] == "state")
        s.send_bytes(FRAME)
        err = _until(s, lambda m: m["type"] == "error")[-1]
        assert err["code"] == "upstream"


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


def test_not_configured_without_a_provider_or_for_one_that_is_not_built_yet(env):
    env.monkeypatch.delenv("VOICE_LIVE_PROVIDER")
    with _live(env) as s:
        assert _read(s)["code"] == "not_configured"
    env.monkeypatch.setenv("VOICE_LIVE_PROVIDER", "gemini")
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
