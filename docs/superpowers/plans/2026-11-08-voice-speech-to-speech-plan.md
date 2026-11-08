# Voice speech-to-speech — Plan

**Date:** 2026-11-08
**Spec:** [2026-11-08-voice-speech-to-speech-design.md](../specs/2026-11-08-voice-speech-to-speech-design.md)
**Rules for every phase:** work in a worktree on a local branch; every new file ≤ 260 lines with one responsibility;
tests first for the protocol and the tool bridge; push / PR / merge / restart only on Laris's explicit word; the
provider key is never printed, logged or committed.

| Phase | What | Needs | Done when |
|---|---|---|---|
| 0 | Decisions + keys (spec §7) | Laris | answers to the 3 questions; a Gemini key in `.env` |
| 1 | Provider-neutral core, `ask_goat` bridge, `/api/voice/live`, `useLiveConversation`, "Live" option — all against a **fake** provider | nothing | a full Live conversation runs in a real browser with the fake provider: transcript bubbles, tool chips, barge-in event, caps; tests green |
| 2 | Gemini 3.8 Live adapter, session resumption, real Romanian check | Gemini key | a Chromium run with a synthetic microphone gets a spoken Romanian answer; **two audio samples sent to Laris** (small talk with emotion, an answer that went through GOAT) |
| 3 | OpenAI gpt-realtime-2 adapter, A/B on the same script | OpenAI key | Laris has heard both and chosen |
| 4 | Barge-in (full duplex) with echo cancellation, filler tuning, "must call ask_goat" eval list | phone test | interruption works with a headset and, if it can, with the phone speaker; the eval list passes |
| 5 | Hardening: minutes counter on the Dashboard, caps, fallbacks to the Deepgram path, docs, removal of dead code | — | restart-safe, documented, merged |

## Phase 1 in detail (starts now)

1. `admin_panel/voice_live/provider.py` — neutral event types and the adapter protocol. `fake.py` — a scripted
   provider (it "hears" a number of frames, then emits a transcript, a tool call, audio and `turn_complete`).
2. `admin_panel/voice_live/bridge.py` — `ask_goat`: `ChatService.submit(transcript)`, wait for the turn, return its
   reply (or "busy" / "error" text); emits tool events to the page.
3. `admin_panel/voice_live/session.py` — wires page ⇄ adapter ⇄ bridge, gates microphone frames while the model
   speaks (half-duplex), caps and idle timeout; `admin_panel/routes/voice_live.py` — the WebSocket
   `/api/voice/live`, auth through `voice_ws.authenticate`.
4. Frontend: `useLiveConversation` (mic → PCM16 → socket, socket → gapless Web Audio playback, `interrupted` clears
   the queue), a third voice option "Live", the existing overlay reused for the states.
5. Verification: server tests against the fake provider; frontend tests with fake socket / audio context; a real
   Chromium run with the fake provider and a synthetic microphone.

## Risks to watch while building

- A provider event arriving after the page left (cancel everything in `finally`).
- Two turns racing on the per-chat lock (the bridge returns "busy" instead of queueing).
- Frames arriving faster than they play (bound the playback queue; drop on `interrupted`).
