"""Voice messages through the orchestrator (2026-11-13, spec docs/superpowers/specs/2026-11-13-voice-live-memory-
pipeline-design.md): record → Gemini transcript (the ear only) → a normal GOAT turn marked voice_live with said_at
→ the reply read aloud by the existing Deepgram voice. The voice layer keeps no memory of its own."""
from __future__ import annotations

import asyncio
import base64
import json
import shutil
import subprocess
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

import admin_panel.routes.voice_message as vm
import admin_panel.telegram_auth as auth_module
from admin_panel import gemini_stt
from admin_panel.server import create_app
from orchestrator import orchestrator as orch_mod
from orchestrator.chat_service import ChatBusy, ChatService
from orchestrator.orchestrator import Orchestrator, _archive_turn
from tests._orch_fakes import _FakeAnalytics, _FakeLayers, _FakePluginManager, _LLMClient
from tests.test_admin_panel_voice import _BOT_TOKEN, WEBM, _headers


# ── route ─────────────────────────────────────────────────────────────────────

class _Service:
    def __init__(self, exc=None):
        self.calls, self.exc = [], exc

    def submit(self, text, **kw):
        if self.exc:
            raise self.exc
        self.calls.append((text, kw))
        return SimpleNamespace(id="t1")


@pytest.fixture
def make_client(monkeypatch):
    monkeypatch.setattr("config.settings.TELEGRAM_BOT_TOKEN", _BOT_TOKEN)
    monkeypatch.setattr(auth_module, "load_admin_chat_id", lambda: "42")
    monkeypatch.setattr(auth_module, "AUTH_WRITE_MAX_AGE_SECONDS", 14400)

    def make(service, transcript="Ce am de făcut azi?", stt_exc=None):
        async def fake_transcribe(data):
            if stt_exc:
                raise stt_exc
            return transcript
        monkeypatch.setattr(gemini_stt, "transcribe", fake_transcribe)
        return TestClient(create_app(SimpleNamespace(chat_service=service)))
    return make


def _post(client, said_at=None, headers=None):
    form = {"duration_ms": "4000"}
    if said_at is not None:
        form["said_at"] = str(said_at)
    return client.post("/api/voice/message", files={"audio": ("v.webm", WEBM, "audio/webm")}, data=form,
                       headers=_headers() if headers is None else headers)


def test_requires_write_auth(make_client):
    with make_client(_Service()) as c:
        assert _post(c, headers={}).status_code == 401


def test_transcript_becomes_a_voice_turn(make_client):
    svc = _Service()
    said = time.time() - 5
    with make_client(svc) as c:
        resp = _post(c, said_at=said)
    assert resp.status_code == 200
    assert resp.json() == {"turn_id": "t1", "transcript": "Ce am de făcut azi?"}
    text, kw = svc.calls[0]
    assert text == "Ce am de făcut azi?"
    assert kw["source"] == "voice_live" and kw["voice"] is True
    assert abs(kw["said_at"] - said) < 0.01


def test_nothing_recognised_submits_no_turn(make_client):
    svc = _Service()
    with make_client(svc, transcript="") as c:
        resp = _post(c)
    assert resp.json() == {"turn_id": None, "transcript": ""}
    assert svc.calls == []


def test_errors_map_to_status_codes(make_client):
    with make_client(_Service(), stt_exc=gemini_stt.SttError("could not reach Gemini")) as c:
        resp = _post(c)
        assert resp.status_code == 502 and "Gemini" in resp.json()["detail"]
    with make_client(_Service(exc=ChatBusy())) as c:
        assert _post(c).status_code == 429
    with make_client(None) as c:
        assert _post(c).status_code == 503


def test_said_at_is_clamped():
    now = 1_000_000.0
    assert vm.clamp_said_at(now + 999, 4000, now) == now                       # never in the future
    assert vm.clamp_said_at(now - 10_000, 4000, now) == now - 4 - 60            # not older than clip + a minute
    assert vm.clamp_said_at("garbage", 4000, now) == now - 4                    # unreadable → start of the clip
    assert vm.clamp_said_at(None, 4000, now) == now - 4
    assert vm.clamp_said_at(now - 3, 4000, now) == now - 3


# ── Gemini, the ear ───────────────────────────────────────────────────────────

def test_transcribe_asks_for_a_verbatim_transcript_and_never_for_audio(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["key"] = str(request.url), request.headers.get("x-goog-api-key")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": " Bună, GOAT. "}]}}]})

    async def fake_wav(audio):
        return b"RIFFwav"

    monkeypatch.setenv("GEMINI_API_KEY", "k-test")
    monkeypatch.setenv("GEMINI_STT_MODEL", "stt-model")
    monkeypatch.setattr(gemini_stt, "to_wav", fake_wav)
    text = asyncio.run(gemini_stt.transcribe(b"webm", transport=httpx.MockTransport(handler)))
    assert text == "Bună, GOAT."
    assert seen["url"].endswith("/models/stt-model:generateContent") and seen["key"] == "k-test"
    parts = seen["body"]["contents"][0]["parts"]
    assert parts[0]["text"] == gemini_stt.INSTRUCTION
    assert parts[1]["inline_data"] == {"mime_type": "audio/wav", "data": base64.b64encode(b"RIFFwav").decode()}
    assert "responseModalities" not in json.dumps(seen["body"])           # text only: Gemini never speaks


def test_transcribe_errors_are_safe(monkeypatch):
    async def fake_wav(audio):
        return b"wav"
    monkeypatch.setattr(gemini_stt, "to_wav", fake_wav)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(gemini_stt.SttError, match="not configured"):
        asyncio.run(gemini_stt.transcribe(b"x"))
    monkeypatch.setenv("GEMINI_API_KEY", "secret-key")
    with pytest.raises(gemini_stt.SttError) as exc:
        asyncio.run(gemini_stt.transcribe(b"x", transport=httpx.MockTransport(lambda r: httpx.Response(500, text="boom"))))
    assert "secret-key" not in str(exc.value) and "boom" not in str(exc.value)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_to_wav_converts_any_clip_to_16k_mono():
    src = subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.5",
                          "-c:a", "libopus", "-f", "webm", "pipe:1"], capture_output=True, check=True).stdout
    wav = asyncio.run(gemini_stt.to_wav(src))
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"
    assert int.from_bytes(wav[22:24], "little") == 1 and int.from_bytes(wav[24:28], "little") == 16000


# ── the turn: ChatService → run(), memory ─────────────────────────────────────

def test_chat_service_passes_voice_fields_only_when_given():
    seen = []

    async def run(text, chat_id, **kw):
        seen.append(kw)
        return "ok"

    async def go():
        svc = ChatService(run=run, locks={"c": asyncio.Lock()}, chat_id=lambda: "c")
        await svc.submit("scris").task
        await svc.submit("vorbit", source="voice_live", said_at=123.0, voice=True).task
    asyncio.run(go())
    assert set(seen[0]) == {"on_context_assembled"}
    assert seen[1]["source"] == "voice_live" and seen[1]["said_at"] == 123.0 and seen[1]["voice"] is True


class _Capture(_LLMClient):
    def __init__(self):
        outer = self

        class _C:
            async def create(self, **kw):
                outer.messages = kw["messages"]
                from tests._orch_fakes import _Response
                return _Response("Ai două sarcini azi.")
        super().__init__(_C())


def _turn(monkeypatch, **kw):
    async def fake_retrieve(layers, chat_id, query, state, activation, topic_return_id=None):
        return [], False, None, {}
    monkeypatch.setattr(orch_mod, "retrieve", fake_retrieve)
    layers, llm, archived = _FakeLayers(), _Capture(), {}

    async def store_episodic(chat_id, content, tags=None, topic_id="", doc_id=None, timestamp=None):
        archived.update(content=content, tags=tags, timestamp=timestamp)
        return doc_id or "d"
    layers.store_episodic = store_episodic

    async def go():
        o = Orchestrator(layers, llm, _FakePluginManager(), _FakeAnalytics())
        reply = await o.run("Ce am de făcut azi?", "c", **kw)
        await o.drain_background()
        return reply
    reply = asyncio.run(go())
    return reply, layers, llm.messages, archived


def test_voice_turn_marks_memory_and_asks_for_a_spoken_reply(monkeypatch):
    said = time.time() - 20
    reply, layers, messages, archived = _turn(monkeypatch, source="voice_live", said_at=said, voice=True)
    assert orch_mod._VOICE_REPLY_GUIDANCE in messages[0]["content"]
    user, assistant = layers.saved[-2], layers.saved[-1]
    assert user["content"] == "Ce am de făcut azi?" and user["timestamp"] == said and user["source"] == "voice_live"
    assert assistant["content"] == reply and assistant["source"] == "voice_live"
    assert archived["tags"] == ["l2_full_archive", "voice_live"] and archived["timestamp"] == said
    assert archived["content"] == f"user: Ce am de făcut azi?\nassistant: {reply}"      # verbatim, no summary


def test_typed_turn_is_unchanged(monkeypatch):
    _, layers, messages, archived = _turn(monkeypatch)
    assert orch_mod._VOICE_REPLY_GUIDANCE not in messages[0]["content"]
    assert "source" not in layers.saved[-2] and "source" not in layers.saved[-1]
    assert archived["tags"] == ["l2_full_archive"] and archived["timestamp"] is None


def test_store_episodic_uses_the_given_time(monkeypatch):
    from memory.layers import MemoryLayers
    captured = {}

    class _Ep:
        async def store(self, chat_id, content, metadata, doc_id=None):
            captured.update(content=content, metadata=metadata)
            return "d1"

    layers = MemoryLayers.__new__(MemoryLayers)
    layers._episodic, layers._bm25, layers._pending_bg = _Ep(), None, set()

    async def no_enrich(doc_id, content):
        return None
    layers._enrich_at_write = no_enrich
    said = 1_700_000_000.0
    asyncio.run(layers.store_episodic("c", "user: x\nassistant: y", timestamp=said))
    assert captured["metadata"]["timestamp"] == said and captured["metadata"]["last_accessed_ts"] == said
    from memory.date_format import prefix_with_date
    assert captured["content"] == prefix_with_date("user: x\nassistant: y", said)    # the date shown is when it was said
    asyncio.run(layers.store_episodic("c", "z"))
    assert abs(captured["metadata"]["timestamp"] - time.time()) < 5          # default: now, as before
