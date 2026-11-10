"""admin_panel.voice_live.openai — OpenAI Realtime (``gpt-realtime-2``, speech to speech) as a provider adapter.

Raw WebSocket JSON, no SDK, the GA interface. Checked against OpenAI's docs on 2026-11-10 (developers.openai.com:
guides/voice-websockets, guides/realtime-conversations, reference/resources/realtime/client-events + server-events).
How it differs from Gemini Live, and what that means here:

- ``wss://api.openai.com/v1/realtime?model=…`` with ``Authorization: Bearer``; no ``OpenAI-Beta`` header (GA).
- One ``session.update`` (``session.type: "realtime"``) sets instructions, voice, tools (flat ``{type: "function",
  name, description, parameters}``), audio formats, transcription and turn detection; it is answered by
  ``session.updated`` or an ``error``.
- Input PCM is accepted **only at 24 kHz**: the page's 16 kHz microphone is resampled here (``resample.Resampler``).
- History: user text items are documented (``input_text``), assistant text items are not, so the recent chat goes
  into the instructions as context instead.
- Turn detection is ``semantic_vad`` with low eagerness (it waits for the end of a thought, not a fixed silence:
  Laris found a fixed pause too short); a person talking over the model cancels its response (``interrupt_response``,
  default true): ``input_audio_buffer.speech_started`` during a response is passed on as ``Interrupted``.
- A function call ends its response. The model only speaks the result after a ``function_call_output`` item **and** a
  ``response.create``; ``WHEN_IDLE`` sends both (the ``response.create`` waits for a running response to finish, one
  response at a time), ``SILENT`` sends only the item. There is no call-cancellation event (``ToolCancel`` never comes).
- A tap sends ``response.cancel`` and drops the rest of that response's audio; ``response.done`` ends every response
  (``TurnComplete``, plus ``Usage`` from ``response.usage.total_tokens``).
- ``error`` events are mostly recoverable ("the session will stay open"): logged, not fatal. Only the connection
  closing ends the session.
- No ``goAway`` and no session resumption; a session lasts up to 60 minutes, beyond the panel's 15 minute cap.
  ``resume()`` therefore raises. Romanian is not on a published list for this model (only the A/B by ear tells).
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
    Audio, AssistantText, Event, HistoryItem, Interrupted, LiveConfig, ProviderError, ToolCall, TurnComplete, Usage,
    UserText,
)
from admin_panel.voice_live.resample import Resampler
from utils.logging.setup import get_logger

log = get_logger(__name__)

__all__ = ["OpenAIProvider", "make_openai", "DEFAULT_VOICE", "RATE"]

DEFAULT_VOICE = "marin"  # one of the two voices OpenAI recommends for quality (with "cedar")
RATE = 24000  # the only PCM rate the Realtime API accepts, in and out
_SETUP_TIMEOUT = 15.0
_WHO = {"user": "Laris", "model": "GOAT"}


class OpenAIProvider:
    def __init__(self, api_key: str, model: str, url: str) -> None:
        self._key, self._model, self._url = api_key, model, url
        self._ws = None
        self._queue: asyncio.Queue[Event | None] = asyncio.Queue()
        self._reader: asyncio.Task | None = None
        self._closing = False
        self._up: Resampler | None = None
        self._heard: dict[str, str] = {}  # what the person said so far, per conversation item
        self._active: str | None = None  # the response being generated now
        self._dropping: set[str] = set()  # responses cancelled by a tap: their remaining audio is stale
        self._speak_after = False  # a result is waiting for the running response to finish

    # ── Provider ──────────────────────────────────────────────────────────────

    async def connect(self, config: LiveConfig, history: list[HistoryItem]) -> None:
        self._up = Resampler(config.in_rate, RATE)
        self._ws = await websockets.connect(
            f"{self._url}?model={self._model}", additional_headers={"Authorization": f"Bearer {self._key}"},
            open_timeout=10, max_size=2**23,
        )
        await self._send({"type": "session.update", "session": self._session(config, history)})
        while True:  # session.created first, then the answer to our update
            message = json.loads(await asyncio.wait_for(self._ws.recv(), _SETUP_TIMEOUT))
            if message.get("type") == "session.updated":
                break
            if message.get("type") == "error":
                raise RuntimeError("the live voice service refused the session setup")
        self._reader = asyncio.create_task(self._read())

    async def resume(self, handle: str) -> None:
        raise NotImplementedError("OpenAI Realtime has no session resumption")

    async def send_audio(self, pcm: bytes) -> None:
        up = self._up(pcm) if self._up else pcm
        if up:
            await self._send({"type": "input_audio_buffer.append", "audio": base64.b64encode(up).decode()})

    async def send_tool_result(self, call_id: str, name: str, result: str, scheduling: str) -> None:
        await self._send({"type": "conversation.item.create",
                          "item": {"type": "function_call_output", "call_id": call_id, "output": result}})
        if scheduling == "SILENT":
            return
        if self._active is None:
            await self._send({"type": "response.create"})
        else:
            self._speak_after = True

    async def interrupt(self) -> None:
        if self._active is not None:
            self._dropping.add(self._active)
            await self._send({"type": "response.cancel"})

    async def set_mic(self, on: bool) -> None:
        """Nothing to do: the server's voice activity detection decides when a turn ends."""

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

    def _session(self, config: LiveConfig, history: list[HistoryItem]) -> dict:
        instructions = config.system_prompt
        if history:
            lines = "\n".join(f"{_WHO.get(item.role, item.role)}: {item.text}" for item in history)
            instructions += f"\n\n# The conversation so far (context only, do not answer it)\n{lines}"
        pcm = {"type": "audio/pcm", "rate": RATE}
        return {
            "type": "realtime",
            "instructions": instructions,
            "output_modalities": ["audio"],
            "audio": {
                "input": {
                    "format": pcm,
                    "transcription": {"model": "gpt-4o-transcribe", "language": config.language.split("-")[0]},
                    "turn_detection": {"type": "semantic_vad", "eagerness": "low"},
                },
                "output": {"format": pcm, "voice": config.voice or DEFAULT_VOICE},
            },
            "tools": [{"type": "function", **tool} for tool in config.tools],
            "tool_choice": "auto",
        }

    async def _send(self, message: dict) -> None:
        if self._ws is not None and not self._closing:
            await self._ws.send(json.dumps(message))

    async def _read(self) -> None:
        reason = "the live voice connection closed"
        try:
            async for raw in self._ws:
                try:
                    message = json.loads(raw)
                except ValueError:
                    continue
                for event in self._events_for(message):
                    self._queue.put_nowait(event)
                if self._speak_after and self._active is None:
                    self._speak_after = False
                    await self._send({"type": "response.create"})
        except asyncio.CancelledError:
            raise
        except websockets.ConnectionClosed as exc:
            reason = re.sub(r"(sk-|Bearer )[^\s'\"]+", r"\1<key>", f"the live voice connection closed ({exc.rcvd.code if exc.rcvd else 'no code'})")
        except Exception as exc:  # noqa: BLE001
            log.warning("voice_live openai: reader failed: %s", type(exc).__name__)
        if not self._closing:
            self._queue.put_nowait(ProviderError("closed", reason))

    def _events_for(self, message: dict) -> list[Event]:
        """A Realtime server event as neutral events."""
        kind = message.get("type", "")
        if kind == "response.created":
            self._active = (message.get("response") or {}).get("id") or "?"
        elif kind == "response.output_audio.delta":
            if message.get("delta") and message.get("response_id") not in self._dropping:
                return [Audio(base64.b64decode(message["delta"]))]
        elif kind == "response.output_audio_transcript.delta":
            if message.get("delta") and message.get("response_id") not in self._dropping:
                return [AssistantText(message["delta"])]
        elif kind == "conversation.item.input_audio_transcription.delta":
            item = str(message.get("item_id", ""))
            self._heard[item] = self._heard.get(item, "") + str(message.get("delta", ""))
            return [UserText(self._heard[item].strip(), final=False)]
        elif kind == "conversation.item.input_audio_transcription.completed":
            self._heard.pop(str(message.get("item_id", "")), None)
            return [UserText(str(message.get("transcript", "")).strip(), final=True)]
        elif kind == "input_audio_buffer.speech_started":
            if self._active is not None and self._active not in self._dropping:
                return [Interrupted()]  # the person talks over the model; the server cancels the response
        elif kind == "response.function_call_arguments.done":
            try:
                args = json.loads(message.get("arguments") or "{}")
            except ValueError:
                args = {}
            return [ToolCall(id=str(message.get("call_id", "")), name=str(message.get("name", "")),
                             args=args if isinstance(args, dict) else {})]
        elif kind == "response.done":
            response = message.get("response") or {}
            self._dropping.discard(response.get("id"))
            self._active = None
            events: list[Event] = [TurnComplete()]
            total = (response.get("usage") or {}).get("total_tokens")
            if total:
                events.append(Usage(int(total)))
            return events
        elif kind == "error":
            error = message.get("error") or {}
            log.warning("voice_live openai: %s (%s)", error.get("code") or error.get("type"), str(error.get("message", ""))[:200])
        return []


def make_openai() -> OpenAIProvider | None:
    key = voice_config.openai_key()
    if key is None:
        return None
    return OpenAIProvider(key, voice_config.openai_realtime_model(), voice_config.openai_realtime_url())
