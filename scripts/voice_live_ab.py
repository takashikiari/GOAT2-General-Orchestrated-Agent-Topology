"""scripts.voice_live_ab — the same Romanian lines through Gemini Live and OpenAI Realtime, for Laris to judge by ear.

BLOCKED until both keys are in GOAT's ``.env``: ``GEMINI_API_KEY`` and ``OPENAI_API_KEY`` (the script says so and stops).
``DEEPGRAM_API_KEY`` speaks the lines: each one is synthesised in Romanian at 16 kHz (what the page's microphone sends),
streamed to the provider in real time, then silence until the provider ends its turn. A line meant for GOAT gets a
fixed answer instead of the real GOAT (nothing reaches the chat, Telegram or memory). For every provider and line it
writes the model's voice as a WAV and a JSON row: what the provider heard, what it said, whether it called
``ask_goat``, the time to its first sound. Synthetic audio only: none of Laris's own voice is sent anywhere.

    source .env && python3 scripts/voice_live_ab.py [--out DIR] [--only gemini|openai]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import wave
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from websockets.asyncio.client import connect  # noqa: E402

from admin_panel import voice_config  # noqa: E402
from admin_panel.voice_live import providers  # noqa: E402
from admin_panel.voice_live.provider import Audio, AssistantText, ProviderError, ToolCall, TurnComplete, UserText  # noqa: E402
from admin_panel.voice_live.session import default_config  # noqa: E402

__all__ = ["LINES", "blocked_reason", "write_wav", "main"]

# (line, GOAT's fixed answer if the model should hand the line to GOAT, else None): small talk with feeling, real
# requests, numbers and dates
LINES: tuple[tuple[str, str | None], ...] = (
    ("Bună! Ce mai faci azi?", None),
    ("Verifică, te rog, dacă au fost erori în loguri în ultima oră.",
     "Am verificat: trei erori de conexiune la Redis între 09:10 și 09:12, rezolvate de la sine."),
    ("Sunt cam obosit azi, dar mă bucur tare că merge vocea.", None),
    ("Câte zile mai sunt până pe treizeci și unu ianuarie două mii douăzeci și șapte?",
     "Din 10 noiembrie 2026 până pe 31 ianuarie 2027 sunt 82 de zile."),
    ("Mulțumesc, asta a fost tot.", None),
)
IN_RATE, FRAME_MS, SILENCE_S, LINE_TIMEOUT_S = 16000, 100, 2.5, 45.0
SETTLE_S = 4.0  # after a tool call: the answer is over once the model has been quiet this long after a spoken turn


def blocked_reason(env=os.environ, only: str | None = None) -> str | None:
    """Why the A/B cannot run yet (None: it can). Each provider needs its key; Deepgram's speaks the lines."""
    needed = [f"{name.upper()}_API_KEY" for name in ([only] if only else ["gemini", "openai"])] + ["DEEPGRAM_API_KEY"]
    missing = [k for k in needed if not env.get(k, "").strip()]
    return f"BLOCKED: missing {', '.join(missing)} in GOAT's .env" if missing else None


def write_wav(path: Path, pcm: bytes, rate: int) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)


async def speak(text: str) -> bytes:
    """The line in Romanian, 16-bit PCM at 16 kHz, from Deepgram's voice."""
    base = voice_config.ws_speak_url().split("?")[0]
    url = f"{base}?{urlencode({'model': voice_config.TTS_MODEL, 'encoding': 'linear16', 'sample_rate': str(IN_RATE)})}"
    pcm = bytearray()
    async with connect(url, additional_headers={"Authorization": f"Token {voice_config.api_key()}"}) as ws:
        await ws.send(json.dumps({"type": "Speak", "text": text}))
        await ws.send(json.dumps({"type": "Flush"}))
        async for message in ws:
            if isinstance(message, bytes):
                pcm += message
            elif json.loads(message).get("type") == "SpeechMetadata":
                break
    return bytes(pcm)


async def one_line(provider, speech: bytes, out_rate: int, goat_answer: str | None) -> dict:
    """Say one line to an open provider and collect its answer until it has finished speaking."""
    heard, said, voice, called = "", [], bytearray(), False
    first_sound: float | None = None
    step = IN_RATE * FRAME_MS // 1000 * 2
    silence = b"\x00\x00" * (IN_RATE * FRAME_MS // 1000)

    async def feed():
        for i in range(0, len(speech), step):
            await provider.send_audio(speech[i:i + step])
            await asyncio.sleep(FRAME_MS / 1000)
        for _ in range(int(SILENCE_S * 1000 / FRAME_MS)):
            await provider.send_audio(silence)
            await asyncio.sleep(FRAME_MS / 1000)

    feeding = asyncio.create_task(feed())
    started = time.monotonic()
    ended_speech = started + len(speech) / 2 / IN_RATE
    result_sent: float | None = None  # when GOAT's (fixed) answer went back
    turn_sound_at: float | None = None  # when the model started speaking the current turn
    settle_until: float | None = None
    stream = provider.events().__aiter__()
    while True:
        wait = None if settle_until is None else max(0.0, settle_until - time.monotonic())
        try:
            event = await asyncio.wait_for(stream.__anext__(), wait)
        except (asyncio.TimeoutError, StopAsyncIteration):
            break  # quiet long enough after the result (or the provider ended)
        if isinstance(event, UserText) and event.final:
            heard = event.text
        elif isinstance(event, AssistantText):
            said.append(event.text)
        elif isinstance(event, Audio):
            first_sound = first_sound or time.monotonic()
            voice += event.pcm
            turn_sound_at = turn_sound_at or time.monotonic()
            settle_until = None  # still talking
        elif isinstance(event, ToolCall):
            called = True
            await provider.send_tool_result(event.id, event.name, goat_answer or "Nu am nimic de spus despre asta.", "WHEN_IDLE")
            result_sent = time.monotonic()
        elif isinstance(event, ProviderError):
            said.append(f"[error: {event.code}]")
            break
        elif isinstance(event, TurnComplete):
            # done: a spoken turn; after a tool call the filler and the spoken result can come in either order, so the
            # answer is over only once the model stays quiet for SETTLE_S after a spoken turn
            if turn_sound_at is not None and result_sent is None and not called:
                break
            if turn_sound_at is not None and result_sent is not None:
                settle_until = time.monotonic() + SETTLE_S
            turn_sound_at = None
    feeding.cancel()
    await asyncio.gather(feeding, return_exceptions=True)
    return {"heard": heard, "said": "".join(said).strip(), "asked_goat": called, "voice": bytes(voice),
            "first_sound_after_speech_s": round(first_sound - ended_speech, 2) if first_sound else None,
            "voice_seconds": round(len(voice) / 2 / out_rate, 1)}


async def run_provider(name: str, speeches: list[bytes], out: Path) -> list[dict]:
    provider = providers.make_provider(name)
    config = default_config(os.environ.get(f"VOICE_AB_{name.upper()}_VOICE") or None)
    rows = []
    await provider.connect(config, [])
    try:
        for i, ((line, goat_answer), speech) in enumerate(zip(LINES, speeches), 1):
            expect_goat = goat_answer is not None
            try:
                row = await asyncio.wait_for(one_line(provider, speech, config.out_rate, goat_answer), LINE_TIMEOUT_S)
            except asyncio.TimeoutError:
                row = {"heard": "", "said": "[timeout]", "asked_goat": False, "voice": b"", "first_sound_after_speech_s": None,
                       "voice_seconds": 0}
            write_wav(out / f"{name}-{i}.wav", row.pop("voice"), config.out_rate)
            rows.append({"provider": name, "line": i, "text": line, "should_ask_goat": expect_goat, **row})
            print(f"{name} {i}: goat={row['asked_goat']} (expected {expect_goat}) first sound {row['first_sound_after_speech_s']} s | {row['said'][:90]}")
    finally:
        await provider.close()
    return rows


async def main(argv: list[str] | None = None) -> int:
    args = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    args.add_argument("--out", default=str(Path.home() / "goat2-eval" / "voice_ab" / time.strftime("%Y%m%d-%H%M%S")))
    args.add_argument("--only", choices=["gemini", "openai"])
    a = args.parse_args(argv)
    reason = blocked_reason(only=a.only)
    if reason:
        print(reason)
        return 2
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    speeches = [await speak(line) for line, _ in LINES]
    rows = []
    for name in ([a.only] if a.only else ["gemini", "openai"]):
        rows += await run_provider(name, speeches, out)
    (out / "results.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(rows)} answers → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
