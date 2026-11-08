"""admin_panel.voice_live.fake — a scripted provider for tests and for trying the page without a real model.

It "hears" an utterance every ``trigger_bytes`` of microphone audio, in the order of ``script``. A ``("tool", text)``
utterance makes it say a short filler and call ``ask_goat``; a ``("chat", text)`` utterance is answered directly.
The audio it speaks is a tone. Events follow Gemini Live's real sequences: transcript, ``toolCall``, audio,
``turnComplete``, ``interrupted``, ``goAway``.
"""
from __future__ import annotations

import array
import asyncio
import math
from typing import AsyncIterator

from admin_panel.voice_live.provider import (
    Audio, AssistantText, Event, GoAway, HistoryItem, Interrupted, LiveConfig, ToolCall, TurnComplete, UserText,
)

__all__ = ["FakeProvider", "DEFAULT_SCRIPT"]

DEFAULT_SCRIPT = (("tool", "verifică logurile din ultima oră"), ("chat", "mulțumesc, e totul"))
_FRAME_MS = 20


class FakeProvider:
    def __init__(self, script=DEFAULT_SCRIPT, *, trigger_bytes: int = 48_000, pace: float = 1.0,
                 tone_seconds: float = 0.8, go_away_after: int | None = None) -> None:
        self.script = list(script)
        self.trigger_bytes = trigger_bytes
        self.pace = pace  # 1.0 = frames leave at real time, 0.0 = as fast as possible (tests)
        self.tone_seconds = tone_seconds
        self.go_away_after = go_away_after  # send GoAway after this many utterances
        self.config: LiveConfig | None = None
        self.history: list[HistoryItem] = []
        self.audio_in = 0
        self.tool_results: list[tuple[str, str, str, str]] = []
        self.interrupts = 0
        self.closed = False
        self._heard = 0
        self._utterances = 0
        self._queue: asyncio.Queue[Event | None] = asyncio.Queue()
        self._speech: asyncio.Task | None = None

    async def connect(self, config: LiveConfig, history: list[HistoryItem]) -> None:
        self.config, self.history = config, list(history)

    async def send_audio(self, pcm: bytes) -> None:
        self.audio_in += len(pcm)
        self._heard += len(pcm)
        if self._heard >= self.trigger_bytes and self.script:
            self._heard = 0
            kind, text = self.script.pop(0)
            self._utterances += 1
            self._start(self._hear(kind, text))

    async def send_tool_result(self, call_id: str, name: str, result: str, scheduling: str) -> None:
        self.tool_results.append((call_id, name, result, scheduling))
        if scheduling != "SILENT":
            self._start(self._say(f"Am aflat: {result[:120]}"))

    async def interrupt(self) -> None:
        self.interrupts += 1
        if self._speech is not None and not self._speech.done():
            self._speech.cancel()
        self._queue.put_nowait(Interrupted())

    async def events(self) -> AsyncIterator[Event]:
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event

    async def close(self) -> None:
        self.closed = True
        if self._speech is not None and not self._speech.done():
            self._speech.cancel()
        self._queue.put_nowait(None)

    # ── scripted behaviour ────────────────────────────────────────────────────

    def _start(self, coro) -> None:
        self._speech = asyncio.ensure_future(coro)

    async def _hear(self, kind: str, text: str) -> None:
        words = text.split()
        self._queue.put_nowait(UserText(" ".join(words[:2]), final=False))
        self._queue.put_nowait(UserText(text, final=True))
        if self.go_away_after is not None and self._utterances >= self.go_away_after:
            self._queue.put_nowait(GoAway(seconds=5.0))
        if kind == "tool":
            await self._say("O secundă, verific.")
            self._queue.put_nowait(ToolCall(id=f"call-{self._utterances}", name="ask_goat", args={"request": text}))
        else:
            await self._say(f"Cu plăcere. Ai spus: {text}")

    async def _say(self, text: str) -> None:
        self._queue.put_nowait(AssistantText(text))
        rate = self.config.out_rate if self.config else 24000
        samples = int(rate * self.tone_seconds)
        tone = array.array("h", (int(8000 * math.sin(2 * math.pi * 440 * i / rate)) for i in range(samples)))
        data = tone.tobytes()
        step = int(rate * _FRAME_MS / 1000) * 2
        for i in range(0, len(data), step):
            self._queue.put_nowait(Audio(data[i:i + step]))
            if self.pace:
                await asyncio.sleep(_FRAME_MS / 1000 * self.pace)
            else:
                await asyncio.sleep(0)
        self._queue.put_nowait(TurnComplete())
