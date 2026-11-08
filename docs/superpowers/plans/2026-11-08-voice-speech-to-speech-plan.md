# Voice speech-to-speech — Plan

**Date:** 2026-11-08
**Spec:** [2026-11-08-voice-speech-to-speech-design.md](../specs/2026-11-08-voice-speech-to-speech-design.md)
**Rules for every phase:** work in a worktree on a local branch; every new file ≤ 260 lines with one responsibility;
tests first for the protocol and the tool bridge; push / PR / merge / restart only on Laris's explicit word; the
provider key is never printed, logged or committed.

| Phase | What | Needs | Done when |
|---|---|---|---|
| 0 | Listening gate + decisions + keys (spec §7) | Laris | he tried both playgrounds in Romanian and answered the 3 questions; a Gemini key in `.env` |
| 1 | Provider-neutral core, `ask_goat` bridge, `/api/voice/live`, `useLiveConversation`, "Live" option — all against a **fake** provider | nothing | a full Live conversation runs in a real browser with the fake provider: transcript bubbles, tool chips, tap-to-interrupt, caps; the fake replays Gemini's real event sequences (turn with tool call, interruption, tool cancel, goAway); tests green |
| 2 | Gemini 3.8 Live adapter, session resumption, real Romanian check | Gemini key | a Chromium run with a synthetic microphone gets a spoken Romanian answer; **two audio samples sent to Laris** (small talk with emotion, an answer that went through GOAT) |
| 3 | OpenAI gpt-realtime-2 adapter, A/B on the same script | OpenAI key | Laris has heard both and chosen |
| 4 | Barge-in by voice (full duplex) with echo cancellation, filler tuning, "must call ask_goat" eval list | phone test; ee1c5e's notes from #41 on echo and the pause | interruption works with a headset and, if it can, with the phone speaker; the eval list passes |
| 5 | Hardening: minutes counter on the Dashboard, caps, fallbacks to the Deepgram path, docs, removal of dead code | — | restart-safe, documented, merged |

## Phase 1 in detail (done locally 2026-11-08, not pushed)

1. `admin_panel/voice_live/provider.py` — neutral event types and the adapter protocol, from Gemini's real
   messages (spec §3). `fake.py` — a scripted provider that replays those sequences.
2. `admin_panel/voice_live/bridge.py` — `ask_goat(request)`: `ChatService.submit`, wait for the turn, return its
   reply (or "busy" / "error" text) with `WHEN_IDLE` scheduling, `SILENT` if the person interrupted meanwhile;
   emits tool events to the page; seeds the session history from the recent chat.
3. `admin_panel/voice_live/session.py` — wires page ⇄ adapter ⇄ bridge, gates microphone frames while the model
   speaks, handles the page's `interrupt`, caps and idle timeout; `admin_panel/routes/voice_live.py` — the WebSocket
   `/api/voice/live`, auth through `voice_ws.authenticate`.
4. Frontend: `useLiveConversation` (mic → PCM16 → socket, socket → gapless Web Audio playback, `interrupted` clears
   the queue, a tap on the overlay sends `interrupt`), a third voice option "Live", the existing overlay reused for
   the states. Coordinate with ee1c5e before touching `useVoiceStream.ts`, `remoteVoice.ts` or `streamConfig.ts`.
5. Verification: server tests against the fake provider; frontend tests with fake socket / audio context; a real
   Chromium run with the fake provider and a synthetic microphone.

## Risks to watch while building

- A provider event arriving after the page left (cancel everything in `finally`).
- Two turns racing on the per-chat lock (the bridge returns "busy" instead of queueing).
- Frames arriving faster than they play (bound the playback queue; drop on `interrupted`).

## Status

- Phase 1 is built and verified against the fake provider: server (`admin_panel/voice_live/`, `WS /api/voice/live`,
  `GET /api/voice/live/status`) and page (`useLiveConversation`, `pcmPlayer`, `useVoiceConversation`, the "Voice: Live"
  option, shown only when the server has a live provider). A real Chromium run with a synthetic microphone: listening,
  the model's filler, "GOAT is working: <tool>", the answer spoken, small talk, tap-to-interrupt.
- Turn it on in a dev environment with `VOICE_LIVE_PROVIDER=fake`. Nothing is on by default.
- Phase 0 decisions (via GOAT, 2026-11-08): natural voice, not cloned; Gemini primary; a Gemini key is in lenovo `.env`.
  Still open: which paid/free tier the key is on (the free tier trains on the data), so only synthetic test audio has
  been sent to Google so far; Laris's own voice goes there only after he confirms.
- Phase 2 is built and verified: the Gemini 3.8 Live adapter through the whole panel in Chromium (synthetic Romanian
  speech, fake GOAT): heard, filler, `ask_goat`, result spoken. Turn it on with `VOICE_LIVE_PROVIDER=gemini` + `GEMINI_API_KEY`.
- Next: Laris listens to the voice samples and picks a voice (`VOICE_LIVE_VOICE`), a real test on his phone, then
  phase 4 (what a phone allows) and phase 5 (session resumption on goAway, minutes counter, cleanup).
