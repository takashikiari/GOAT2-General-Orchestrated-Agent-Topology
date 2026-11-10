"""tests.test_voice_live_openai — the OpenAI Realtime adapter, against a local fake of the Realtime WebSocket."""
from __future__ import annotations

import array
import asyncio
import base64
import contextlib
import json

import pytest
from websockets.asyncio.server import serve

from admin_panel.voice_live import openai as oa
from admin_panel.voice_live import prompt, providers
from admin_panel.voice_live.provider import (
    Audio, AssistantText, HistoryItem, Interrupted, LiveConfig, ProviderError, ToolCall, TurnComplete, Usage, UserText,
)

KEY = "sk-fake-openai-key-123"
CONFIG = LiveConfig(system_prompt="be brief", tools=(prompt.ASK_GOAT,), voice="cedar")


class FakeRealtime:
    """The Realtime WebSocket: records what the adapter sends; the test pushes server events."""

    def __init__(self, refuse_update: bool = False):
        self.received: list[dict] = []
        self.path = ""
        self.headers: dict = {}
        self.refuse_update = refuse_update
        self.ws = None
        self._connected = asyncio.Event()

    async def handler(self, ws):
        self.ws, self.path, self.headers = ws, ws.request.path, dict(ws.request.headers)
        await ws.send(json.dumps({"type": "session.created", "session": {}}))
        self._connected.set()
        async for raw in ws:
            msg = json.loads(raw)
            self.received.append(msg)
            if msg.get("type") == "session.update":
                if self.refuse_update:
                    await ws.send(json.dumps({"type": "error", "error": {"type": "invalid_request_error", "message": "bad"}}))
                else:
                    await ws.send(json.dumps({"type": "session.updated", "session": msg["session"]}))

    async def push(self, *events: dict) -> None:
        await self._connected.wait()
        for event in events:
            await self.ws.send(json.dumps(event))

    def sent(self, kind: str) -> list[dict]:
        return [m for m in self.received if m.get("type") == kind]

    async def wait_for(self, kind: str, count: int = 1, seconds: float = 2.0) -> list[dict]:
        end = asyncio.get_event_loop().time() + seconds
        while asyncio.get_event_loop().time() < end:
            if len(self.sent(kind)) >= count:
                return self.sent(kind)
            await asyncio.sleep(0.01)
        raise AssertionError(f"never received {count} x {kind}: {[m.get('type') for m in self.received]}")


@contextlib.asynccontextmanager
async def realtime(**fake_kwargs):
    fake = FakeRealtime(**fake_kwargs)
    async with serve(fake.handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        provider = oa.OpenAIProvider(KEY, "gpt-realtime-2", f"ws://127.0.0.1:{port}/v1/realtime")
        try:
            yield fake, provider
        finally:
            await provider.close()


async def take(provider, n, seconds=2.0):
    events = []

    async def collect():
        async for event in provider.events():
            events.append(event)
            if len(events) >= n:
                return
    await asyncio.wait_for(collect(), seconds)
    return events


def _b64(pcm: bytes) -> str:
    return base64.b64encode(pcm).decode()


@pytest.mark.asyncio
async def test_connect_picks_the_model_authenticates_and_sets_up_a_romanian_audio_session_with_the_tool():
    async with realtime() as (fake, provider):
        await provider.connect(CONFIG, [HistoryItem("user", "salut"), HistoryItem("model", "bună")])
        session = fake.sent("session.update")[0]["session"]
    assert fake.path == "/v1/realtime?model=gpt-realtime-2"
    assert fake.headers.get("authorization") == f"Bearer {KEY}" and "openai-beta" not in fake.headers
    assert session["type"] == "realtime" and session["output_modalities"] == ["audio"]
    assert session["audio"]["input"]["format"] == {"type": "audio/pcm", "rate": 24000}  # the only rate it accepts
    assert session["audio"]["input"]["transcription"]["language"] == "ro"
    assert session["audio"]["input"]["turn_detection"]["type"] == "semantic_vad"
    assert session["audio"]["output"] == {"format": {"type": "audio/pcm", "rate": 24000}, "voice": "cedar"}
    assert session["tools"] == [{"type": "function", **prompt.ASK_GOAT}] and session["tool_choice"] == "auto"
    # the recent chat is context in the instructions (assistant text items are not documented as accepted)
    assert session["instructions"].startswith("be brief") and "Laris: salut" in session["instructions"]
    assert "GOAT: bună" in session["instructions"]


@pytest.mark.asyncio
async def test_a_refused_session_setup_is_an_error_that_does_not_contain_the_key():
    async with realtime(refuse_update=True) as (_fake, provider):
        with pytest.raises(RuntimeError) as err:
            await provider.connect(CONFIG, [])
    assert KEY not in str(err.value)


@pytest.mark.asyncio
async def test_16_khz_microphone_audio_goes_up_as_24_khz_and_the_models_voice_comes_back():
    async with realtime() as (fake, provider):
        await provider.connect(CONFIG, [])
        await provider.send_audio(array.array("h", [1000] * 1600).tobytes())  # 0.1 s at 16 kHz
        sent = (await fake.wait_for("input_audio_buffer.append"))[0]
        await fake.push({"type": "response.created", "response": {"id": "r1"}},
                        {"type": "response.output_audio.delta", "response_id": "r1", "delta": _b64(b"\x05\x00" * 4)})
        events = await take(provider, 1)
    up = base64.b64decode(sent["audio"])
    assert abs(len(up) - 2400 * 2) <= 2 and set(array.array("h", up)) == {1000}  # 0.1 s at 24 kHz, same level
    assert events == [Audio(b"\x05\x00" * 4)]


@pytest.mark.asyncio
async def test_what_was_heard_builds_up_and_becomes_final_and_what_the_model_says_is_text_too():
    async with realtime() as (fake, provider):
        await provider.connect(CONFIG, [])
        await fake.push(
            {"type": "conversation.item.input_audio_transcription.delta", "item_id": "i1", "delta": "verifică "},
            {"type": "conversation.item.input_audio_transcription.delta", "item_id": "i1", "delta": "logurile"},
            {"type": "conversation.item.input_audio_transcription.completed", "item_id": "i1", "transcript": "Verifică logurile."},
            {"type": "response.output_audio_transcript.delta", "response_id": "r1", "delta": "O secundă."},
        )
        events = await take(provider, 4)
    assert events == [UserText("verifică", False), UserText("verifică logurile", False),
                      UserText("Verifică logurile.", True), AssistantText("O secundă.")]


@pytest.mark.asyncio
async def test_a_tool_call_comes_out_and_its_result_makes_the_model_speak_unless_it_is_silent():
    async with realtime() as (fake, provider):
        await provider.connect(CONFIG, [])
        await fake.push({"type": "response.function_call_arguments.done", "call_id": "c1", "name": "ask_goat",
                         "arguments": json.dumps({"request": "verifică logurile"})})
        assert await take(provider, 1) == [ToolCall("c1", "ask_goat", {"request": "verifică logurile"})]
        await provider.send_tool_result("c1", "ask_goat", "trei erori", "WHEN_IDLE")
        items = await fake.wait_for("conversation.item.create")
        await fake.wait_for("response.create")
        await provider.send_tool_result("c2", "ask_goat", "ignorat", "SILENT")
        await fake.wait_for("conversation.item.create", 2)
        await asyncio.sleep(0.05)
    assert items[0]["item"] == {"type": "function_call_output", "call_id": "c1", "output": "trei erori"}
    assert len(fake.sent("response.create")) == 1  # the silent result is known, not spoken


@pytest.mark.asyncio
async def test_a_result_that_arrives_while_the_model_is_speaking_is_spoken_after_that_response():
    async with realtime() as (fake, provider):
        await provider.connect(CONFIG, [])
        await fake.push({"type": "response.created", "response": {"id": "r1"}})
        await asyncio.sleep(0.05)
        await provider.send_tool_result("c1", "ask_goat", "gata", "WHEN_IDLE")
        await fake.wait_for("conversation.item.create")
        await asyncio.sleep(0.05)
        assert fake.sent("response.create") == []  # one response at a time
        await fake.push({"type": "response.done", "response": {"id": "r1", "status": "completed", "usage": {"total_tokens": 7}}})
        await fake.wait_for("response.create")


@pytest.mark.asyncio
async def test_a_tap_cancels_the_response_and_drops_its_remaining_audio_but_not_the_next_ones():
    async with realtime() as (fake, provider):
        await provider.connect(CONFIG, [])
        await fake.push({"type": "response.created", "response": {"id": "r1"}})
        await asyncio.sleep(0.05)
        await provider.interrupt()
        await fake.wait_for("response.cancel")
        await fake.push(
            {"type": "response.output_audio.delta", "response_id": "r1", "delta": _b64(b"\x01\x00")},
            {"type": "response.done", "response": {"id": "r1", "status": "cancelled", "status_details": {"reason": "client_cancelled"}}},
            {"type": "response.created", "response": {"id": "r2"}},
            {"type": "response.output_audio.delta", "response_id": "r2", "delta": _b64(b"\x02\x00")},
        )
        events = await take(provider, 2)
    assert events == [TurnComplete(), Audio(b"\x02\x00")]


@pytest.mark.asyncio
async def test_the_person_talking_over_the_model_interrupts_it_and_the_end_of_a_response_carries_the_usage():
    async with realtime() as (fake, provider):
        await provider.connect(CONFIG, [])
        await fake.push(
            {"type": "input_audio_buffer.speech_started", "item_id": "i0"},  # nothing being said: not an interruption
            {"type": "response.created", "response": {"id": "r1"}},
            {"type": "input_audio_buffer.speech_started", "item_id": "i2"},
            {"type": "response.done", "response": {"id": "r1", "status": "cancelled", "usage": {"total_tokens": 321}}},
        )
        events = await take(provider, 3)
    assert events == [Interrupted(), TurnComplete(), Usage(321)]


@pytest.mark.asyncio
async def test_a_recoverable_error_event_keeps_the_session_and_a_dropped_connection_is_reported_once():
    async with realtime() as (fake, provider):
        await provider.connect(CONFIG, [])
        await fake.push({"type": "error", "error": {"type": "invalid_request_error", "code": "x", "message": "nope"}},
                        {"type": "response.output_audio_transcript.delta", "response_id": "r", "delta": "încă aici"})
        assert await take(provider, 1) == [AssistantText("încă aici")]
        await fake.ws.close()
        events = await take(provider, 1)
    assert isinstance(events[0], ProviderError) and KEY not in events[0].message


@pytest.mark.asyncio
async def test_there_is_no_session_resumption_and_the_microphone_signal_needs_nothing():
    async with realtime() as (fake, provider):
        await provider.connect(CONFIG, [])
        await provider.set_mic(False)
        with pytest.raises(NotImplementedError):
            await provider.resume("h")
        await asyncio.sleep(0.05)
    assert [m["type"] for m in fake.received] == ["session.update"]


def test_the_factory_needs_a_key_and_reads_the_model_and_url_from_the_environment(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert oa.make_openai() is None and providers.make_provider("openai") is None
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    monkeypatch.setenv("OPENAI_REALTIME_MODEL", "gpt-realtime-2.1")
    monkeypatch.setenv("OPENAI_REALTIME_URL", "ws://127.0.0.1:9/rt")
    p = providers.make_provider("openai")
    assert isinstance(p, oa.OpenAIProvider) and p._model == "gpt-realtime-2.1" and p._url == "ws://127.0.0.1:9/rt"
    monkeypatch.delenv("OPENAI_REALTIME_MODEL")
    monkeypatch.delenv("OPENAI_REALTIME_URL")
    p = oa.make_openai()
    assert p._model == "gpt-realtime-2" and p._url == "wss://api.openai.com/v1/realtime"
