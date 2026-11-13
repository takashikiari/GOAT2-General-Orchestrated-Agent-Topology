# Voice messages through the orchestrator — design (v3)

**Date:** 2026-11-13 · **Status:** spec v3, awaiting Laris's approval (no code, no push before it) · **Todo:** T30
**Base:** main `378f99d` · Replaces v1 (option B) and v2 (orchestrator-driven live call).

## Decisions (Laris, 2026-11-13, via GOAT 09:26)

1. **One fixed voice.** The Romanian Edge/Azure voice Laris has already heard is used everywhere (phone, Telegram,
   notifications). Gemini Live's audio output is cut completely: Gemini is only the ear. The "give the model the text
   and let it read it" variant and its fidelity measurement are dropped.
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
        → reply text → TTS (the fixed ro-RO voice) → audio/mpeg → page plays it           ← sound stops
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

### 5. The voice: one fixed Romanian voice (`admin_panel/voice_message/tts.py`, new)

- Reply text → audio (mp3) with the chosen ro-RO neural voice.
- The text is cleaned for speech first: markdown stripped; numbers, dates and times written in words. The existing
  Deepgram route already does this with `admin_panel/ro_speech.py` (`normalize`, line 163; `number_to_words`, line
  58), imported by `routes/voice_speak.py:24`. That module is reused as is.
- Iteration 1 returns the whole clip once synthesized (replies are short, §3). Sentence-by-sentence streaming
  (speak the first sentence while the rest synthesizes) is a later optimisation.
- One module, one voice id in config (`[voice_message] tts_voice = "ro-RO-…Neural"`). Telegram voice replies and
  notifications reuse it later (decision 1: one voice everywhere).

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
  endpoint starts TTS as soon as `run()` returns, and nothing else memory-related sits between the reply and the
  audio.
- This will be measured: the log line `voice_message: timings stt=… turn=… tts=… total=…` per message. If the L2
  write ever shows up in the timings, it moves behind the audio with an explicit ordering guard.

### 8. API (`admin_panel/routes/voice_message.py`, new)

- `POST /api/voice/message`: multipart (audio, said_at), write-auth like `/api/chat`. It transcribes, submits, and
  returns `{"turn_id", "transcript"}`. The page then polls the existing `/api/chat/turns/{id}`.
- `GET /api/voice/reply/{turn_id}`: when the turn is done, the TTS audio (`audio/mpeg`) of its reply. It is
  synthesized on first request and kept in memory for a few minutes.

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
  - the returned turn id;
  - the reply endpoint is 404/409 until the turn is done and returns `audio/mpeg` after.
- **ChatService / run:** `source`, `said_at` and `voice` reach `run()`; the defaults leave every existing caller
  unchanged.
- **Memory:**
  - the L2 user message has the `said_at` timestamp and `source=voice_live`;
  - `_archive_turn` is called with the `voice_live` tag and `timestamp=said_at`;
  - `store_episodic(timestamp=)` sets the metadata and the date prefix, with the default unchanged.
- **Voice prompt line:** present with `voice=True`, absent by default.
- **TTS:**
  - markdown, numbers and dates are cleaned before synthesis;
  - the configured voice id is used;
  - a failure returns the text-only reply (the chat still shows it) and an error event.
- **Timing:** the log line is written, and no memory call happens between `run()` returning and TTS starting.
- **Page:**
  - tap to start / tap to send;
  - cancel;
  - the working sound loops from send to first audio and stops on error;
  - the mic constraints are unchanged (PR #47 regression).
- **Regression:** Telegram, typed panel chat, Live voice and the Deepgram voice are unchanged.

## Open questions (for Laris)

1. **Which voice, exactly, and through what?** Nothing in the GOAT2 repo or on this machine uses an Edge/Azure voice
   today. The only TTS in code is Deepgram (`routes/voice_speak.py`). Laris needs to tell us:
   - **which voice it is:** `ro-RO-AlinaNeural` (female) or `ro-RO-EmilNeural` (male), or where he heard it, so I
     can find the id;
   - **which access path:**
     - (a) **Azure Speech**: official, needs a key and region, paid beyond the free tier;
     - (b) **the Edge read-aloud endpoint** (`edge-tts`): the same voices without a key, but an unofficial,
       unsupported use of Microsoft's browser service that can break or be blocked at any time.

   I recommend (a) for something used everywhere.
2. **Telegram voice notes in iteration 1?** Laris sends a voice note in Telegram, and GOAT answers in text plus a
   voice note in the same voice. It reuses STT, the orchestrator and TTS, about one more handler. Proposed: **next
   iteration**, after the panel flow works.
3. **The Gemini Live call and the Deepgram voice in the panel:** hide them behind a switch once the voice message
   works (proposed), or remove them?
4. **Spike (short, real APIs, only with OK):**
   - Gemini transcription of 5 Romanian clips (quality, latency);
   - TTS latency and voice check with the chosen voice.
