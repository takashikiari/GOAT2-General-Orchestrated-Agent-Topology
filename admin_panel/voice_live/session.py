"""admin_panel.voice_live.session — one live voice conversation: the page on one side, a provider on the other.

Messages to the page (JSON text frames, plus binary PCM16 frames of the model's voice at ``out_rate``):
  {"type": "ready", "in_rate", "out_rate"}   {"type": "state", "state": "listening"|"thinking"|"speaking"}
  {"type": "user_text", "text", "final"}     {"type": "assistant_text", "text"}
  {"type": "tool", "name", "status": "running"|"done"|"failed"}
  {"type": "interrupted"}                    drop whatever is queued for playback
  {"type": "notice", "code"}                 the provider will close soon and the session could not be resumed (``go_away``)
  {"type": "error", "code", "message"}       then the socket closes (``daily_limit``: today's minutes are used up)
From the page: binary PCM16 frames at ``in_rate`` (the page itself stops sending while it plays the model's voice, so
the model never hears itself), {"type": "mic", "on": bool} (the microphone opened or closed), {"type": "interrupt"}
(a tap: cut the model off) and {"type": "close"}.
"""
from __future__ import annotations

import asyncio
import json
import time

from admin_panel.voice_live import prompt
from admin_panel.voice_live.bridge import GoatBridge
from admin_panel.voice_live.usage import LiveUsage
from admin_panel.voice_live.provider import (
    Audio, AssistantText, GoAway, HistoryItem, Interrupted, LiveConfig, Provider, ProviderError, Resumption, ToolCall,
    ToolCancel, TurnComplete, Usage, UserText,
)
from utils.logging.setup import get_logger

log = get_logger(__name__)

__all__ = ["run_session", "MAX_FRAME_BYTES", "WATCHDOG_TICK", "default_config"]

MAX_FRAME_BYTES = 64 * 1024
WATCHDOG_TICK = 1.0  # seconds between limit checks
USAGE_FLUSH_SECONDS = 15.0  # how often the minutes talked so far are written down (a crash loses at most this much)


def default_config(voice: str | None = None) -> LiveConfig:
    return LiveConfig(system_prompt=prompt.SYSTEM_PROMPT, tools=(prompt.ASK_GOAT,), voice=voice)


class _Run:
    """The state of one session, shared by its tasks."""

    def __init__(self, ws, provider: Provider, bridge: GoatBridge) -> None:
        self.ws, self.provider, self.bridge = ws, provider, bridge
        self.speaking = False
        self.state = ""
        self.cancelled: set[str] = set()
        self.running: set[asyncio.Task] = set()
        self._settling: asyncio.Future | None = None
        self.last_activity = time.monotonic()
        self.reason = "closed"
        self.handle: str | None = None  # the provider's latest resumption handle
        self.tokens = 0  # the provider's own count for the session so far (logged at the end, next to the time talked)
        self.counted_at = time.monotonic()  # minutes talked are counted from here

    def touch(self) -> None:
        self.last_activity = time.monotonic()

    async def set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            await self.ws.send_json({"type": "state", "state": state})

    async def settle(self) -> None:
        await self.set_state("speaking" if self.speaking else "thinking" if self.running else "listening")

    async def tool(self, call: ToolCall) -> None:
        try:
            if call.name == "ask_goat":
                request = str(call.args.get("request", "")).strip()
                result = await self.bridge.ask(request, self.ws.send_json)
            else:
                result = "Unknown tool."
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the model must hear that it failed, not wait forever
            log.exception("voice_live: ask_goat failed")
            result = "GOAT could not be reached."
        scheduling = "SILENT" if call.id in self.cancelled else "WHEN_IDLE"
        await self.provider.send_tool_result(call.id, call.name, result, scheduling)
        self.touch()

    async def provider_events(self) -> None:
        async for event in self.provider.events():
            if isinstance(event, Audio):
                self.touch()
                self.speaking = True
                await self.set_state("speaking")
                await self.ws.send_bytes(event.pcm)
            elif isinstance(event, UserText):
                self.touch()
                await self.ws.send_json({"type": "user_text", "text": event.text, "final": event.final})
            elif isinstance(event, AssistantText):
                self.touch()
                await self.ws.send_json({"type": "assistant_text", "text": event.text})
            elif isinstance(event, ToolCall):
                self.touch()
                task = asyncio.create_task(self.tool(event))
                self.running.add(task)
                task.add_done_callback(self._tool_done)
                await self.settle()
            elif isinstance(event, ToolCancel):
                self.cancelled.update(event.ids)
            elif isinstance(event, Interrupted):
                self.speaking = False
                await self.ws.send_json({"type": "interrupted"})
                await self.settle()
            elif isinstance(event, TurnComplete):
                self.speaking = False
                await self.settle()
            elif isinstance(event, Resumption):
                self.handle = event.handle
            elif isinstance(event, Usage):
                self.tokens = max(self.tokens, event.total_tokens)
            elif isinstance(event, GoAway):
                if not await self.resume():
                    await self.ws.send_json({"type": "notice", "code": "go_away", "seconds": event.seconds})
            elif isinstance(event, ProviderError):
                log.warning("voice_live: provider error %s", event.code)
                self.reason = "provider_error"
                await self.ws.send_json({"type": "error", "code": "upstream", "message": event.message})
                return
        self.reason = "provider_closed"

    async def resume(self) -> bool:
        """The provider will close this connection: carry on in a new one, unseen by the page. False if it could not."""
        if not self.handle:
            log.info("voice_live: go_away without a resumption handle")
            return False
        try:
            await self.provider.resume(self.handle)
        except Exception as exc:  # noqa: BLE001 - the old connection runs out; the page is told
            log.warning("voice_live: resume failed: %s", type(exc).__name__)
            return False
        log.info("voice_live: resumed in a new connection")
        return True

    def _tool_done(self, task: asyncio.Task) -> None:
        self.running.discard(task)
        self._settling = asyncio.ensure_future(self._settle_quietly())

    async def _settle_quietly(self) -> None:
        try:
            await self.settle()
        except Exception:  # noqa: BLE001 - the page may be gone already
            pass

    async def page_messages(self, max_frame: int) -> None:
        while True:
            message = await self.ws.receive()
            if message["type"] == "websocket.disconnect":
                return
            if message.get("bytes") is not None:
                if len(message["bytes"]) > max_frame:
                    self.reason = "frame_too_large"
                    await self.ws.send_json({"type": "error", "code": "frame_too_large", "message": "audio frame too large"})
                    return
                await self.provider.send_audio(message["bytes"])
            elif message.get("text") is not None:
                try:
                    control = json.loads(message["text"])
                except ValueError:
                    continue
                kind = control.get("type") if isinstance(control, dict) else None
                if kind == "close":
                    return
                if kind == "interrupt":
                    await self.provider.interrupt()
                elif kind == "mic":
                    await self.provider.set_mic(bool(control.get("on")))

    def count(self, usage: LiveUsage | None) -> None:
        """Write down the time talked since the last count."""
        now = time.monotonic()
        if usage is not None and now > self.counted_at:
            usage.add(now - self.counted_at)
        self.counted_at = now

    async def watchdog(self, idle_seconds: float, max_seconds: float, usage: LiveUsage | None, left: float) -> None:
        started = time.monotonic()
        while True:
            await asyncio.sleep(WATCHDOG_TICK)
            now = time.monotonic()
            if now - self.counted_at >= USAGE_FLUSH_SECONDS:
                self.count(usage)
            if now - started >= left:
                self.reason = "daily_limit"
                await self.ws.send_json({"type": "error", "code": "daily_limit", "message": "today's live voice minutes are used up"})
                return
            if now - started >= max_seconds:
                self.reason = "time_limit"
                await self.ws.send_json({"type": "error", "code": "time_limit", "message": "the conversation reached its time limit"})
                return
            if not self.running and now - self.last_activity >= idle_seconds:
                self.reason = "idle"
                await self.ws.send_json({"type": "error", "code": "idle", "message": "ended after a stretch of silence"})
                return


async def run_session(ws, provider: Provider, bridge: GoatBridge, config: LiveConfig, history: list[HistoryItem], *,
                      idle_seconds: float, max_seconds: float, max_frame: int = MAX_FRAME_BYTES,
                      usage: LiveUsage | None = None, left_seconds: float = float("inf")) -> str:
    """Run the conversation until the page leaves, the provider ends it, or a limit is hit (``left_seconds``: what is left
    of today's minutes; the time talked is added to ``usage``). Returns why it ended."""
    run = _Run(ws, provider, bridge)
    await provider.connect(config, history)
    run.counted_at = connected = time.monotonic()  # billed from the moment the provider is connected
    if usage is not None:
        usage.add(0, session=True)
    tasks: list[asyncio.Task] = []
    try:
        await ws.send_json({"type": "ready", "in_rate": config.in_rate, "out_rate": config.out_rate})
        await run.settle()
        tasks = [
            asyncio.create_task(run.page_messages(max_frame)),
            asyncio.create_task(run.provider_events()),
            asyncio.create_task(run.watchdog(idle_seconds, max_seconds, usage, left_seconds)),
        ]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in tasks:
            if task.done() and not task.cancelled() and task.exception() is not None:
                log.info("voice_live: ended by %s", type(task.exception()).__name__)
                run.reason = "disconnected"
    finally:
        for task in [*tasks, *run.running]:
            task.cancel()
        await asyncio.gather(*tasks, *run.running, return_exceptions=True)
        run.count(usage)
        log.info("voice_live: %.0f s talked, %d provider tokens (%s)", time.monotonic() - connected, run.tokens, run.reason)
        try:
            await provider.close()
        except Exception:  # noqa: BLE001
            pass
    return run.reason
