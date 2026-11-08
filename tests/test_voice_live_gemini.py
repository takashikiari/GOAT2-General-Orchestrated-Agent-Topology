"""tests.test_voice_live_gemini — the Gemini Live adapter, against a local fake of the Live API's WebSocket."""
from __future__ import annotations

import asyncio
import base64
import contextlib
import json

import pytest
from websockets.asyncio.server import serve

from admin_panel import voice_config
from admin_panel.voice_live import prompt, providers
from admin_panel.voice_live.gemini import GeminiProvider, make_gemini
from admin_panel.voice_live.provider import (
    Audio, AssistantText, GoAway, HistoryItem, Interrupted, LiveConfig, ProviderError, Resumption, ToolCall, ToolCancel,
    TurnComplete, Usage, UserText,
)

KEY = "AQ.fake-gemini-key-123"
CONFIG = LiveConfig(system_prompt="be brief", tools=(prompt.ASK_GOAT,), voice="Charon")


class FakeLive:
    """The Live API's WebSocket: records what the adapter sends; the test pushes server messages."""

    def __init__(self, answer_setup: bool = True):
        self.received: list[dict] = []
        self.path = ""
        self.answer_setup = answer_setup
        self.ws = None
        self._connected = asyncio.Event()

    async def handler(self, ws):
        self.ws, self.path = ws, ws.request.path
        self._connected.set()
        async for raw in ws:
            msg = json.loads(raw)
            self.received.append(msg)
            if "setup" in msg and self.answer_setup:
                await ws.send(json.dumps({"setupComplete": {}}))

    async def push(self, message: dict) -> None:
        await self._connected.wait()
        await self.ws.send(json.dumps(message))

    async def wait_for(self, key: str, seconds: float = 2.0) -> dict:
        end = asyncio.get_event_loop().time() + seconds
        while asyncio.get_event_loop().time() < end:
            for msg in self.received:
                if key in msg:
                    return msg
            await asyncio.sleep(0.01)
        raise AssertionError(f"never received {key}: {self.received}")


@contextlib.asynccontextmanager
async def live(provider_kwargs=None, **fake_kwargs):
    fake = FakeLive(**fake_kwargs)
    async with serve(fake.handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        provider = GeminiProvider(KEY, "gemini-3.8-live", f"ws://127.0.0.1:{port}/ws/live")
        try:
            yield fake, provider
        finally:
            await provider.close()


async def take(provider, n, seconds=2.0):
    events = []
    gen = provider.events()
    async def collect():
        async for event in gen:
            events.append(event)
            if len(events) >= n:
                return
    await asyncio.wait_for(collect(), seconds)
    return events


def audio_part(pcm: bytes) -> dict:
    return {"modelTurn": {"parts": [{"inlineData": {"mimeType": "audio/pcm;rate=24000", "data": base64.b64encode(pcm).decode()}}]}}


@pytest.mark.asyncio
async def test_setup_asks_for_romanian_speech_with_the_tool_the_prompt_and_transcription():
    async with live() as (fake, provider):
        await provider.connect(CONFIG, [])
        setup = fake.received[0]["setup"]
    assert fake.path == f"/ws/live?key={KEY}"  # the key travels in the query string, nowhere else
    assert setup["model"] == "models/gemini-3.8-live"
    gen = setup["generationConfig"]
    assert gen["responseModalities"] == ["AUDIO"]
    assert gen["speechConfig"] == {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": "Charon"}}, "languageCode": "ro-RO"}
    assert setup["systemInstruction"]["parts"][0]["text"] == "be brief"
    declaration = setup["tools"][0]["functionDeclarations"][0]
    assert declaration["name"] == "ask_goat" and declaration["behavior"] == "NON_BLOCKING"  # keeps talking while GOAT works
    assert setup["inputAudioTranscription"] == {} and setup["outputAudioTranscription"] == {}


@pytest.mark.asyncio
async def test_the_session_starts_from_the_recent_chat_as_context_only():
    async with live() as (fake, provider):
        await provider.connect(CONFIG, [HistoryItem("user", "ce ai făcut?"), HistoryItem("model", "Am verificat logurile.")])
        content = (await fake.wait_for("clientContent"))["clientContent"]
    assert content["turnComplete"] is False  # no reply to the history itself
    assert [(t["role"], t["parts"][0]["text"]) for t in content["turns"]] == [("user", "ce ai făcut?"), ("model", "Am verificat logurile.")]


@pytest.mark.asyncio
async def test_a_refused_setup_is_an_error_that_does_not_contain_the_key():
    async with live(answer_setup=False) as (fake, provider):
        async def refuse():
            await fake._connected.wait()
            await fake.ws.send(json.dumps({"error": {"message": f"bad key {KEY}"}}))
        asyncio.ensure_future(refuse())
        with pytest.raises(RuntimeError) as info:
            await provider.connect(CONFIG, [])
    assert KEY not in str(info.value)


@pytest.mark.asyncio
async def test_audio_goes_up_as_base64_pcm_and_the_models_voice_comes_back():
    async with live() as (fake, provider):
        await provider.connect(CONFIG, [])
        await provider.send_audio(b"\x01\x00" * 1600)
        sent = (await fake.wait_for("realtimeInput"))["realtimeInput"]["audio"]
        assert sent["mimeType"] == "audio/pcm;rate=16000" and base64.b64decode(sent["data"]) == b"\x01\x00" * 1600
        await fake.push({"serverContent": audio_part(b"\x07\x00" * 480)})
        assert await take(provider, 1) == [Audio(b"\x07\x00" * 480)]


@pytest.mark.asyncio
async def test_what_was_heard_builds_up_and_becomes_final_when_the_model_starts_answering():
    async with live() as (fake, provider):
        await provider.connect(CONFIG, [])
        await fake.push({"serverContent": {"inputTranscription": {"text": "Verifică "}}})
        await fake.push({"serverContent": {"inputTranscription": {"text": "logurile."}}})
        await fake.push({"serverContent": {"outputTranscription": {"text": "Sigur, verific."}}})
        events = await take(provider, 4)
    assert events == [UserText("Verifică ", False), UserText("Verifică logurile.", False), UserText("Verifică logurile.", True), AssistantText("Sigur, verific.")]


@pytest.mark.asyncio
async def test_a_tool_call_comes_out_and_its_result_goes_back_with_the_scheduling():
    async with live() as (fake, provider):
        await provider.connect(CONFIG, [])
        await fake.push({"toolCall": {"functionCalls": [{"id": "fc-1", "name": "ask_goat", "args": {"request": "verifică logurile"}}]}})
        assert await take(provider, 1) == [ToolCall("fc-1", "ask_goat", {"request": "verifică logurile"})]
        await provider.send_tool_result("fc-1", "ask_goat", "trei erori", "WHEN_IDLE")
        response = (await fake.wait_for("toolResponse"))["toolResponse"]["functionResponses"][0]
    assert response == {"id": "fc-1", "name": "ask_goat", "response": {"result": "trei erori", "scheduling": "WHEN_IDLE"}}


@pytest.mark.asyncio
async def test_cancelled_calls_goaway_resumption_usage_interruption_and_the_end_of_a_turn_are_passed_on():
    async with live() as (fake, provider):
        await provider.connect(CONFIG, [])
        for message in (
            {"toolCallCancellation": {"ids": ["fc-1"]}}, {"goAway": {"timeLeft": "4.5s"}},
            {"sessionResumptionUpdate": {"newHandle": "h-1", "resumable": True}}, {"usageMetadata": {"totalTokenCount": 1234}},
            {"serverContent": {"interrupted": True}}, {"serverContent": {"turnComplete": True}},
            {"voiceActivity": {"voiceActivityType": "ACTIVITY_START"}},  # a message we do not know: ignored
        ):
            await fake.push(message)
        events = await take(provider, 6)
    assert events == [ToolCancel(("fc-1",)), GoAway(4.5), Resumption("h-1"), Usage(1234), Interrupted(), TurnComplete()]


@pytest.mark.asyncio
async def test_a_tap_drops_the_rest_of_a_response_that_is_still_being_generated_but_never_the_next_one():
    async with live() as (fake, provider):
        await provider.connect(CONFIG, [])
        await fake.push({"serverContent": audio_part(b"\x01\x00" * 10)})
        assert await take(provider, 1) == [Audio(b"\x01\x00" * 10)]
        await provider.interrupt()  # the model is mid-response
        await fake.push({"serverContent": audio_part(b"\x02\x00" * 10)})  # the rest of it: dropped
        await fake.push({"serverContent": {"turnComplete": True}})
        await fake.push({"serverContent": audio_part(b"\x03\x00" * 10)})  # the next response: kept
        events = await take(provider, 2)
        await fake.push({"serverContent": {"turnComplete": True}})  # the model is idle now
        events += await take(provider, 1)
        await provider.interrupt()  # a tap while the model is idle changes nothing
        await fake.push({"serverContent": audio_part(b"\x04\x00" * 10)})
        events += await take(provider, 1)
    assert events == [TurnComplete(), Audio(b"\x03\x00" * 10), TurnComplete(), Audio(b"\x04\x00" * 10)]


@pytest.mark.asyncio
async def test_pausing_the_microphone_flushes_the_models_cached_audio_and_resuming_sends_nothing():
    async with live() as (fake, provider):
        await provider.connect(CONFIG, [])
        await provider.set_mic(True)
        await provider.set_mic(False)
        await fake.wait_for("realtimeInput")
    assert [m for m in fake.received if "realtimeInput" in m] == [{"realtimeInput": {"audioStreamEnd": True}}]


@pytest.mark.asyncio
async def test_a_connection_that_drops_is_reported_once_and_a_deliberate_close_is_not():
    async with live() as (fake, provider):
        await provider.connect(CONFIG, [])
        await fake.ws.close()
        events = await take(provider, 1)
    assert isinstance(events[0], ProviderError) and events[0].code == "closed" and KEY not in events[0].message
    async with live() as (fake, provider):
        await provider.connect(CONFIG, [])
        await provider.close()
        assert [e async for e in provider.events()] == []


def test_the_factory_needs_a_key_and_reads_the_model_and_url_from_the_environment(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert make_gemini() is None and providers.is_available("gemini") is False
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    monkeypatch.setenv("GEMINI_LIVE_MODEL", "gemini-3.8-live-extended-thinking")
    provider = make_gemini()
    assert provider is not None and providers.is_available("gemini") is True
    assert provider._model == "gemini-3.8-live-extended-thinking"
    assert voice_config.gemini_live_url().startswith("wss://generativelanguage.googleapis.com/ws/")
