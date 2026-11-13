# Voice messages through the orchestrator — design (v3)

**Date:** 2026-11-13 · **Status:** spec v3 (voice decided: Deepgram Flux Cosmin), awaiting Laris's approval (no code, no push before it) · **Todo:** T30
**Base:** main `378f99d` · Replaces v1 (option B) and v2 (orchestrator-driven live call).

## Decisions (Laris, 2026-11-13, via GOAT 09:26)

1. **One fixed voice: Deepgram Flux `flux-cosmin-ro`** (Laris: Flux at 09:37, Cosmin confirmed at 09:40). It is the
   voice already configured in the live `.env` and already heard in the panel, on the existing Deepgram key: no new
   key, vendor or `.env` change. It is used everywhere (phone, Telegram, notifications). Azure / Edge are not considered now.
   Gemini Live's audio output is cut completely: Gemini is only the ear. The "give the model the text and let it read
   it" variant and its fidelity measurement are dropped.
2. **Same thread.** Spoken turns go into the existing Telegram conversation, not a separate "voice" thread. Only the
   channel differs (voice vs text). Memory and context come from the orchestrator as for any message; the voice
   layer keeps no memory of its own.
3. **No dead silence.** While the orchestrator thinks (3-8 s), a short "working" sound plays.
4. **Iteration 1 = a simple voice message.** Record, send, get an audio reply. No live open-mic streaming and no
   barge-in in this iteration.
5. **Code:** written by this session (ee1c5e) alone.

Kept from v2:
- the transcription carries `said_at` and the `voice_live` tag;
- the memory write runs after the audio has left for the speaker, so it never adds seconds to the turn.

## Flow (iteration 1)

```
page: hold/tap mic → record (MediaRecorder, opus/webm) → stop ─┐
                                                               │ POST /api/voice/message (audio, said_at)
server: transcribe (Gemini = ear) → ChatService.submit(transcript, source="voice_live", said_at, voice=True)
        → Orchestrator.run (same chat, lane human, same lock, tools, memory)            ← page plays the "working" sound
        → reply text → page opens the EXISTING /api/voice/speak/stream (Deepgram Flux, flux-cosmin-ro)
          → PCM 24 kHz streamed → page plays it (existing remoteVoice.ts)                ← sound stops
        → (background, after the audio is sent) memory writes complete
```

The chat page shows the transcript and the reply as a normal turn. Telegram gets the same pair (one conversation,
§4).

## Components

### 1. Recording and sending (frontend, `admin_panel/frontend/src/voice/`)

- A mic button in Chat. **Tap to start, tap to send** (iteration 1). A pause never cuts the message, which was
  Laris's complaint about the old voice, where pauses ended the message early. Cancel = discard. Max length: config,
  default 120 s.
- `MediaRecorder` (opus in webm/ogg). The mic constraints stay as fixed in PR #47 (no call processing), so playback
  stays at media volume.
- `said_at` = the moment the recording started (client clock, sent with the audio; the server clamps it to "not in
  the future, not older than the clip length + 60 s").

### 2. The ear: transcription with Gemini (`admin_panel/voice_message/stt.py`, new)

- One `generateContent` call with the clip as inline audio and a strict instruction: verbatim Romanian transcript,
  no answer, no summary, no punctuation fixes beyond sentence breaks. It uses the existing Gemini key/config
  (`voice_config`). It is not a Live session: there is no streaming in iteration 1, and Gemini's audio output is
  never requested.
- An empty or unclear result returns "n-am înțeles" to the page as text and audio (TTS), and no turn is submitted.
- **Unverified:** latency and transcript quality for Romanian clips. This is spike item 2.

### 3. The turn: the orchestrator, unchanged except three small parameters

`ChatService.submit(text, *, source=None, said_at=None, voice=False)` → `Orchestrator.run(..., source=, said_at=,
voice=)`. These are additive keyword arguments with defaults, so every existing caller is unchanged.

- `voice=True` adds one line to the system prompt: the answer will be spoken. That means plain Romanian sentences,
  no markdown, lists, code or links, numbers and dates written so they read well, short by default, with details in
  the chat if they don't fit.
- Everything else is the normal turn: classify_turn on lane human, retrieve, L1/L2/L3 context, AITS, tools,
  post-turn prefetch. The voice layer holds no memory and builds no context of its own (decision 2).

### 4. Memory: the same thread, the real time, a tag

- **L2** (`orchestrator.py:626-630`): the user's message is the transcript, with `"timestamp": said_at` and
  `"source": "voice_live"`; the assistant's message is the reply (what is spoken).
- **L3** (`_archive_turn`, `orchestrator.py:178`): tags `l2_full_archive,voice_live`, and `timestamp=said_at`. This
  needs an optional `timestamp` on `_archive_turn` and `layers.store_episodic`; today it is `time.time()` at write,
  `memory/layers.py:193`, and the default stays unchanged.
- **Telegram copy:** `ChatService` already mirrors panel turns (`chat_service.py:121/132`), so the voice turn
  appears in Telegram as text, in the same conversation.

### 5. The voice: Deepgram Flux `flux-cosmin-ro`, reusing what is already there (nothing new to build)

- **Route:** the existing WebSocket `/api/voice/speak/stream` (`admin_panel/routes/voice_speak.py:53`). The page
  sends `{"type": "speak", "text": <reply>}`. The route opens Deepgram's streaming TTS (`wss://api.deepgram.com/v2/speak`,
  `voice_config.py:65`: model `TTS_MODEL`, linear16, 24 kHz) and streams the PCM back. The page already plays it
  (`frontend/src/voice/remoteVoice.ts:32`). Audio starts with the first chunk, not after the whole clip, so
  streaming comes for free.
- **Text cleanup** (markdown, and numbers/dates/times written in words) is already done on that path by
  `admin_panel/ro_speech.py` (`normalize`, line 163), imported at `voice_speak.py:24`. Reused as is.
- **Key / switch:** `DEEPGRAM_API_KEY` (present in `.env`) and `voice_config.tts_enabled()` (`voice_config.py:68-70`,
  on unless `DEEPGRAM_TTS=off`; not set in `.env`, so on). No new key, module or vendor.
- **The voice id:** `voice_config.TTS_MODEL` = env `DEEPGRAM_TTS_MODEL` (`voice_config.py:24-25`). The live `.env`
  sets `flux-cosmin-ro`, which is the voice Laris has heard and kept (09:40). Nothing changes, in code or in `.env`.
  The code default `flux-ioana-ro` only applies on a machine without that line.
- Telegram voice notes and notifications reuse the same `TTS_MODEL` later (decision 1: one voice everywhere).
- Text length: the route refuses texts over `voice_config.TTS_MAX_CHARS` (`voice_speak.py:47`). Spoken replies are
  short (§3); if a reply is longer, the page speaks the part up to the limit and says the rest is in the chat.

### 6. The "working" sound (frontend)

- From the moment the recording is sent until the reply audio starts: a short, soft looping cue (e.g. one low tone
  every ~1.5 s, or a gentle pulse). It is a client-side asset, never synthesized and never sent anywhere. It stops
  the instant playback begins, or on error.
- The visual "GOAT is thinking…" pulse stays, and tool progress (existing turn events) stays visual.

### 7. Memory after the audio (decision: no seconds added to the turn)

What happens before the reply text exists cannot be deferred: transcription and the orchestrator turn. After `run()`
returns:

- **The L3 archive, its enrichment and chunking, auto-promote and the post-turn prefetch** already run as background
  tasks (`orchestrator.py:631-636`, `:667`). They never delay the reply.
- **The L2 append** (`append_and_save_working_context`, `:626`) runs inside `run()` before it returns. It is one
  Redis read-modify-write under the per-chat lock, so milliseconds, not seconds. It must stay before the next turn
  can start, otherwise the next turn would read L2 without this exchange. So it stays where it is. The voice
  page opens the speak stream as soon as the turn's reply arrives, and nothing else memory-related sits between the
  reply and the audio.
- This will be measured: the log line `voice_message: timings stt=… turn=… tts=… total=…` per message. If the L2
  write ever shows up in the timings, it moves behind the audio with an explicit ordering guard.

### 8. API (`admin_panel/routes/voice_message.py`, new)

- `POST /api/voice/message`: multipart (audio, said_at), write-auth like `/api/chat`. It transcribes, submits, and
  returns `{"turn_id", "transcript"}`. The page then polls the existing `/api/chat/turns/{id}`.
- No reply-audio endpoint: once the turn is `done`, the page sends its reply text to the existing
  `/api/voice/speak/stream` (§5).

## Removed / unchanged in iteration 1

- Unchanged and not used by this flow: the Gemini Live call (`voice_live/*`, routes) and the Deepgram turn-by-turn
  routes. Whether to hide or retire them is open question 3.
- Nothing from option B is used. The saved B patch is dropped, except the optional `timestamp` idea, which is
  rewritten here.

## Tests

- **STT:**
  - the request carries the audio and the verbatim instruction;
  - an empty or unclear result submits no turn and returns "n-am înțeles";
  - no audio-output request is ever made to Gemini.
- **API:**
  - auth required;
  - size and length limits;
  - `said_at` is clamped;
  - the returned turn id.
- **ChatService / run:** `source`, `said_at` and `voice` reach `run()`; the defaults leave every existing caller
  unchanged.
- **Memory:**
  - the L2 user message has the `said_at` timestamp and `source=voice_live`;
  - `_archive_turn` is called with the `voice_live` tag and `timestamp=said_at`;
  - `store_episodic(timestamp=)` sets the metadata and the date prefix, with the default unchanged.
- **Voice prompt line:** present with `voice=True`, absent by default.
- **Voice (existing route, unchanged):**
  - the page sends the done turn's reply to `/api/voice/speak/stream` exactly once;
  - a speak error leaves the text reply in the chat and stops the working sound;
  - the route's existing tests stay green.
- **Timing:** the log line is written, and no memory call happens between `run()` returning and TTS starting.
- **Page:**
  - tap to start / tap to send;
  - cancel;
  - the working sound loops from send to first audio and stops on error;
  - the mic constraints are unchanged (PR #47 regression).
- **Regression:** Telegram, typed panel chat, Live voice and the Deepgram voice are unchanged.

## Open questions (for Laris)

1. ~~Ioana or Cosmin?~~ **Resolved (Laris, 09:40): Cosmin**, `flux-cosmin-ro`, the voice already in the live `.env`.
   No `.env` change. Azure and Edge-TTS are out for now, by his choice.
2. **Telegram voice notes in iteration 1?** Laris sends a voice note in Telegram, and GOAT answers in text plus a
   voice note in the same voice. It reuses STT, the orchestrator and TTS, about one more handler. Proposed: **next
   iteration**, after the panel flow works.
3. **The Gemini Live call and the Deepgram voice in the panel:** hide them behind a switch once the voice message
   works (proposed), or remove them?
4. **Spike (short, real APIs, only with OK):**
   - Gemini transcription of 5 Romanian clips (quality, latency);
   - TTS latency and voice check with the chosen voice.
