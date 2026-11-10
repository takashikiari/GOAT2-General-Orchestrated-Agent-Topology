"""admin_panel.voice_live.gemini — Gemini Live (native speech to speech) as a provider adapter.

Raw WebSocket JSON, no SDK. Findings from the real API (2026-11-08, ``gemini-3.8-live``): the model generates faster
than real time, so a tap finds the response already (almost) fully sent; ``interrupted`` only comes from the model's own
voice activity detection. ``interrupt()`` therefore only drops what is still being generated; the page clears its own
playback. Function calls are non-blocking: the model keeps talking while GOAT works and says the result when idle.
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
from typing import AsyncIterator

import websockets

from admin_panel import voice_config
from admin_panel.voice_live.provider import (
    Audio, AssistantText, Event, GoAway, HistoryItem, Interrupted, LiveConfig, ProviderError, Resumption, ToolCall,
    ToolCancel, TurnComplete, Usage, UserText,
)
from utils.logging.setup import get_logger

log = get_logger(__name__)

__all__ = ["GeminiProvider", "make_gemini"]

DEFAULT_VOICE = "Kore"
_SETUP_TIMEOUT = 15.0


class GeminiProvider:
    def __init__(self, api_key: str, model: str, url: str) -> None:
        self._key, self._model, self._url = api_key, model, url
        self._ws = None
        self._config: LiveConfig | None = None
        self._queue: asyncio.Queue[Event | None] = asyncio.Queue()
        self._reader: asyncio.Task | None = None
        self._closing = False
        self._heard = ""  # what the person said so far in this turn (the provider sends it in pieces)
        self._responding = False  # the model is generating a response right now
        self._discarding = False  # the person tapped: drop the rest of this response

    # ── Provider ──────────────────────────────────────────────────────────────

    async def connect(self, config: LiveConfig, history: list[HistoryItem]) -> None:
        self._config = config
        self._ws = await self._open(config, None)
        if history:
            turns = [{"role": item.role, "parts": [{"text": item.text}]} for item in history]
            await self._send({"clientContent": {"turns": turns, "turnComplete": False}})  # context only, no reply
        self._reader = asyncio.create_task(self._read(self._ws))

    async def resume(self, handle: str) -> None:
        """Carry the session on in a new connection (Google keeps the conversation behind ``handle``). If the new one
        cannot be opened this raises and the current connection stays as it is."""
        new = await self._open(self._config, handle)
        old, old_reader = self._ws, self._reader
        self._ws = new  # from here on everything goes to, and comes from, the new connection
        self._reader = asyncio.create_task(self._read(new))
        self._responding = self._discarding = False
        if old is not None:
            try:
                await old.close()  # its reader ends quietly: it is no longer the current connection
            except Exception:  # noqa: BLE001
                pass
        if old_reader is not None:
            old_reader.cancel()
            await asyncio.gather(old_reader, return_exceptions=True)

    async def send_audio(self, pcm: bytes) -> None:
        rate = self._config.in_rate if self._config else 16000
        audio = {"data": base64.b64encode(pcm).decode(), "mimeType": f"audio/pcm;rate={rate}"}
        await self._send({"realtimeInput": {"audio": audio}})

    async def send_tool_result(self, call_id: str, name: str, result: str, scheduling: str) -> None:
        response = {"result": result, "scheduling": scheduling}
        await self._send({"toolResponse": {"functionResponses": [{"id": call_id, "name": name, "response": response}]}})

    async def interrupt(self) -> None:
        if self._responding:
            self._discarding = True

    async def set_mic(self, on: bool) -> None:
        if not on:  # audio paused: flush what the model has cached of it
            await self._send({"realtimeInput": {"audioStreamEnd": True}})

    async def events(self) -> AsyncIterator[Event]:
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event

    async def close(self) -> None:
        self._closing = True
        if self._reader is not None:
            self._reader.cancel()
            await asyncio.gather(self._reader, return_exceptions=True)
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001
                pass
        self._queue.put_nowait(None)

    # ── internals ─────────────────────────────────────────────────────────────

    async def _open(self, config: LiveConfig, handle: str | None):
        """A connection with the session set up (``handle``: continue that session); raises if it is refused."""
        ws = await websockets.connect(f"{self._url}?key={self._key}", open_timeout=10, max_size=2**23)
        try:
            await ws.send(json.dumps(self._setup(config, handle)))
            first = json.loads(await asyncio.wait_for(ws.recv(), _SETUP_TIMEOUT))
            if "setupComplete" not in first:
                raise RuntimeError("the live voice service refused the session setup")
        except BaseException:
            await ws.close()
            raise
        return ws

    def _setup(self, config: LiveConfig, handle: str | None = None) -> dict:
        tools = [{"functionDeclarations": [{**tool, "behavior": "NON_BLOCKING"} for tool in config.tools]}]
        speech = {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": config.voice or DEFAULT_VOICE}}, "languageCode": config.language}
        return {"setup": {
            "model": f"models/{self._model}",
            "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": speech},
            "systemInstruction": {"parts": [{"text": config.system_prompt}]},
            "tools": tools,
            "inputAudioTranscription": {},
            "outputAudioTranscription": {},
            # a handle now and then, so the conversation can outlive one connection (about ten minutes each)
            "sessionResumption": {"handle": handle} if handle else {},
            # older turns are compressed instead of the audio session ending at its 15 minute limit
            "contextWindowCompression": {"slidingWindow": {}},
        }}

    async def _send(self, message: dict) -> None:
        if self._ws is not None and not self._closing:
            await self._ws.send(json.dumps(message))

    async def _read(self, ws) -> None:
        reason = "the live voice connection closed"
        try:
            async for raw in ws:
                try:
                    message = json.loads(raw)
                except ValueError:
                    continue
                for event in self._events_for(message):
                    self._queue.put_nowait(event)
        except asyncio.CancelledError:
            raise
        except websockets.ConnectionClosed as exc:
            reason = re.sub(r"key=[^&\s]+", "key=<key>", f"the live voice connection closed ({exc.rcvd.code if exc.rcvd else 'no code'})")
        except Exception as exc:  # noqa: BLE001
            log.warning("voice_live gemini: reader failed: %s", type(exc).__name__)
        if not self._closing and ws is self._ws:  # a connection replaced by resume() ends quietly
            self._queue.put_nowait(ProviderError("closed", reason))

    def _events_for(self, message: dict) -> list[Event]:
        """Gemini's message as neutral events."""
        events: list[Event] = []
        content = message.get("serverContent") or {}
        said = (content.get("inputTranscription") or {}).get("text")
        if said:
            self._heard += said
            events.append(UserText(self._heard, final=False))
        for part in (content.get("modelTurn") or {}).get("parts", []):
            data = (part.get("inlineData") or {}).get("data")
            if data:
                self._close_user_turn(events)
                self._responding = True
                if not self._discarding:
                    events.append(Audio(base64.b64decode(data)))
        spoken = (content.get("outputTranscription") or {}).get("text")
        if spoken and not self._discarding:
            self._close_user_turn(events)
            events.append(AssistantText(spoken))
        if content.get("interrupted"):
            self._responding = self._discarding = False
            events.append(Interrupted())
        if content.get("turnComplete") or content.get("generationComplete"):
            self._responding = self._discarding = False
            if content.get("turnComplete"):
                self._close_user_turn(events)  # a late piece of the person's words still ends up final
                events.append(TurnComplete())
        for call in (message.get("toolCall") or {}).get("functionCalls", []):
            self._close_user_turn(events)
            events.append(ToolCall(id=str(call.get("id", "")), name=str(call.get("name", "")), args=call.get("args") or {}))
        cancelled = (message.get("toolCallCancellation") or {}).get("ids")
        if cancelled:
            events.append(ToolCancel(tuple(str(i) for i in cancelled)))
        if "goAway" in message:
            events.append(GoAway(seconds=_seconds((message["goAway"] or {}).get("timeLeft"))))
        update = message.get("sessionResumptionUpdate") or {}
        if update.get("newHandle") and update.get("resumable", True) is not False:
            events.append(Resumption(str(update["newHandle"])))
        total = (message.get("usageMetadata") or {}).get("totalTokenCount")
        if total:
            events.append(Usage(int(total)))
        return events

    def _close_user_turn(self, events: list[Event]) -> None:
        """The model started answering: what the person said is complete."""
        if self._heard:
            events.append(UserText(self._heard, final=True))
            self._heard = ""


def _seconds(duration) -> float:
    """"5s" / "4.5s" (a protobuf duration) as seconds."""
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)s\s*", str(duration or ""))
    return float(match.group(1)) if match else 0.0


def make_gemini() -> GeminiProvider | None:
    key = voice_config.gemini_key()
    if key is None:
        return None
    return GeminiProvider(key, voice_config.gemini_live_model(), voice_config.gemini_live_url())
