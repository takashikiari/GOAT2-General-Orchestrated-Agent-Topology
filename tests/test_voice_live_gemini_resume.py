"""tests.test_voice_live_gemini_resume — Gemini Live sessions outlive one connection: resumption handles and reconnecting.

A Live connection lasts about ten minutes and ends with ``goAway``; the session carries on in a new connection opened
with the last ``sessionResumptionUpdate`` handle (the conversation is kept on Google's side, nothing is sent again).
"""
from __future__ import annotations

import pytest

from admin_panel.voice_live import gemini
from admin_panel.voice_live.provider import Audio, HistoryItem, ProviderError, Resumption, UserText
from tests.test_voice_live_gemini import CONFIG, audio_part, live, take


def _setups(fake) -> list[dict]:
    return [m["setup"] for m in fake.received if "setup" in m]


@pytest.mark.asyncio
async def test_setup_asks_for_resumption_handles_and_a_sliding_context_window():
    async with live() as (fake, provider):
        await provider.connect(CONFIG, [])
        setup = _setups(fake)[0]
    assert setup["sessionResumption"] == {}  # no handle yet: just ask for them
    assert setup["contextWindowCompression"] == {"slidingWindow": {}}  # audio sessions are not cut at 15 min


@pytest.mark.asyncio
async def test_only_a_resumable_new_handle_is_passed_on():
    async with live() as (fake, provider):
        await provider.connect(CONFIG, [])
        for message in (
            {"sessionResumptionUpdate": {"resumable": False}},  # mid-generation: nothing to resume from
            {"sessionResumptionUpdate": {"newHandle": "h-stale", "resumable": False}},
            {"sessionResumptionUpdate": {"newHandle": "h-2", "resumable": True}},
            {"sessionResumptionUpdate": {"newHandle": "h-3"}},  # resumable left out: usable
        ):
            await fake.push(message)
        events = await take(provider, 2)
    assert events == [Resumption("h-2"), Resumption("h-3")]


@pytest.mark.asyncio
async def test_resume_reconnects_with_the_handle_and_the_conversation_goes_on_in_the_same_event_stream():
    history = [HistoryItem("user", "salut"), HistoryItem("model", "bună")]
    async with live() as (fake, provider):
        await provider.connect(CONFIG, history)
        first = fake.ws
        await fake.push({"serverContent": {"inputTranscription": {"text": "înainte"}}})
        assert await take(provider, 1) == [UserText("înainte", final=False)]

        await provider.resume("h-9")
        assert fake.ws is not first  # a second connection
        setups = _setups(fake)
        assert len(setups) == 2 and setups[1]["sessionResumption"] == {"handle": "h-9"}
        assert setups[1]["systemInstruction"] == setups[0]["systemInstruction"]
        assert sum("clientContent" in m for m in fake.received) == 1  # the history is not sent again

        await provider.send_audio(b"\x01\x00" * 4)
        await fake.wait_for("realtimeInput")  # audio goes to the new connection
        await first.close()  # the old connection ending is expected, not an error
        await fake.push({"serverContent": audio_part(b"\x02\x00" * 8)})
        events = await take(provider, 2)
    # what was heard before the reconnect is kept: it becomes final when the model answers in the new connection
    assert events == [UserText("înainte", final=True), Audio(b"\x02\x00" * 8)]
    assert not any(isinstance(e, ProviderError) for e in events)


@pytest.mark.asyncio
async def test_a_resume_the_service_refuses_raises_and_leaves_the_old_connection_in_place(monkeypatch):
    monkeypatch.setattr(gemini, "_SETUP_TIMEOUT", 0.3)
    async with live(answer_setup=True) as (fake, provider):
        await provider.connect(CONFIG, [])
        first = fake.ws
        fake.answer_setup = False  # the next setup gets no setupComplete

        with pytest.raises(Exception):
            await provider.resume("h-bad")
        fake.ws = first
        await fake.push({"serverContent": {"inputTranscription": {"text": "tot aici"}}})
        assert await take(provider, 1) == [UserText("tot aici", final=False)]


@pytest.mark.asyncio
async def test_the_new_connection_dropping_is_still_reported():
    async with live() as (fake, provider):
        await provider.connect(CONFIG, [])
        await provider.resume("h-1")
        await fake.ws.close()
        events = await take(provider, 1)
    assert isinstance(events[0], ProviderError)
