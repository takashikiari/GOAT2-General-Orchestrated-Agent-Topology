# Voice speech-to-speech — Design

**Date:** 2026-11-08
**Status:** Proposed by the architect session, to be confirmed by Laris (open questions in §7)
**Scope:** Replace the "speech-to-text → GOAT → text-to-speech" loop of the panel's voice conversation with a native
speech-to-speech model that hears and speaks, while GOAT stays the brain.

---

## 1. Why

The conversation shipped in #39/#41 works but feels like a walkie-talkie (Laris, 2026-11-08):

- it waits for a 2.5 s pause before it answers, and the microphone is closed while GOAT speaks, so nobody can interrupt;
- the words are written first and then read by a text-to-speech voice, so there is no emotion and no reaction to how
  the person sounds;
- the whole turn is silent while GOAT thinks (often 5-60 s with tools).

Target: a continuous conversation. A model that works on audio directly hears tone and pauses, answers with natural
prosody, can be interrupted, and says something short ("o secundă, verific") while GOAT works.

## 2. Goals, non-goals, anti-goals

**Goals:** speech in, speech out in Romanian with natural, expressive delivery; interruptible; GOAT's full power (tools,
memory, the shared Telegram conversation) behind it; the key stays on the server; the current Deepgram voice stays as a
working fallback.

**Non-goals:** a second brain (the voice model does not remember, decide or act on its own); cloning Laris's own voice in
the first versions (open question §7); phone calls / SIP; changes to `orchestrator/`.

**Anti-goals (would be a failure even if it technically works):** the voice model answering factual questions about
GOAT's system from its own imagination; facts, decisions or preferences said aloud that never reach GOAT (the prompt
sends them through `ask_goat`; pure small talk handled by the voice model alone is not recorded); a flat voice that is
merely faster.

## 3. Architecture

```
browser                          panel server (admin_panel/voice_live/)                  provider
mic PCM16 16 kHz ──WS──▶  /api/voice/live ──▶ LiveSession ──▶ adapter ──▶ Gemini Live | OpenAI Realtime | fake
speaker ◀── PCM16 24 kHz ◀──────────────────── (audio, events)  ◀──────────────  (speech-to-speech)
                                               │
                                               └─ tool ask_goat(request) ──▶ ChatService.submit()
                                                  same chat, same per-chat lock as Telegram ◀── reply text
```

**Roles.** The voice model is ears and mouth. GOAT is the brain. The model gets one tool, `ask_goat(request)`, and a
short system prompt: speak Romanian, warm and natural, answer pleasantries yourself, send everything that needs
GOAT's knowledge, tools or actions through `ask_goat`, say what GOAT found in your own short spoken words (the full
text is in the chat), never invent facts about the system.

**One turn.** Person speaks → the provider detects the end of the turn (its own VAD, not our 2.5 s timer) → the model
answers or calls `ask_goat` → the server submits the model's `request` argument (it has the whole conversation; the provider's transcript can lag
the call or cover only the last fragment) as a normal chat turn, so it shows in the Chat page and Telegram like any
message, while the page's bubble shows what the provider heard → while it runs the model may speak a short filler, the page shows the tool chips
→ the reply goes back as the tool result → the model speaks it. A second `ask_goat` during a running turn gets
"busy" back immediately (GOAT is one conversation, one turn at a time).

**Server proxy, not browser → provider.** Same pattern as today's `voice_stream`/`voice_speak`: auth in the first
message (`voice_ws.authenticate`), key only on the server, tool calls executed next to `ChatService` (no HTTP round
trips), caps and logs in one place, provider swappable. The extra hop costs ~100 ms; direct browser connections with
ephemeral tokens stay a later optimisation if the tunnel proves slow.

**Browser ↔ server (`WS /api/voice/live`).** Page → server: `auth`, binary PCM16 16 kHz mono frames, `interrupt`
(a tap on the overlay: the server cancels the model's current response), `close`. Server → page: `ready {in_rate,
out_rate}`, binary PCM16 24 kHz, `state {listening|thinking|speaking}`, `user_text {text, final}`,
`assistant_text {text}`, `tool {name, status}`, `interrupted` (drop queued audio), `error {code}`, `time_limit`.

**Provider adapter** (the only provider-specific code, ≤ 260 lines each): `connect(config, history)`,
`send_audio(pcm)`, `send_tool_result(call_id, text, scheduling)`, `interrupt()`, `close()`, and an async stream of
neutral events modelled on what Gemini Live really sends (`serverContent` audio parts, `inputTranscription`,
`outputTranscription`, `interrupted`, `turnComplete`, `toolCall`, `goAway{timeLeft}`,
`sessionResumptionUpdate{handle}`, `usageMetadata`; a tool-call cancellation is handled defensively): `audio`,
`user_text`, `assistant_text`, `tool_call`, `tool_cancel`, `interrupted`, `turn_complete`, `go_away`, `resumption`,
`usage`, `error`. Adapters: `fake` (it replays those sequences, in tests and browser checks), `gemini`, `openai`.

**What changes**

| Stays | Changes | New |
|---|---|---|
| auth (`voice_ws`), `pcmCapture`, Web Audio playback, overlay, Stop/Send slot, push-to-talk `/api/voice`, the Deepgram STT+TTS path as a fallback | the voice choice in the status row gets a third option, "Live"; Live mode does not use `useVoiceStream` / `useConversation` | `admin_panel/voice_live/` (core, adapters, tool bridge, route), `useLiveConversation`, provider key + model in `.env` |

`ro_speech` (numbers in words) is not needed in Live mode: the model reads numbers and dates by itself.

## 4. Provider choice (checked 2026-11-08 in the vendors' own docs)

| | Gemini 3.8 Live | OpenAI gpt-realtime-2 |
|---|---|---|
| Speech to speech, interruptible | yes (own VAD, barge-in) | yes (VAD, barge-in) |
| Romanian | **listed** (`ro`) | not on a published list; multilingual in practice |
| Tool calls while it keeps talking | **yes, non-blocking by default** | function calling, reasoning effort configurable |
| Emotion | native audio, "acoustic nuance detection"; affective-dialog flag removed in 3.8 | steerable by instructions; custom voice from a consented recording |
| Price (audio) | $0.005/min in, $0.018/min out | $32 / $64 per 1M audio tokens (5-10x Gemini per token) |
| Caveats | "preview" label; audio sessions 15 min unless session resumption; free tier trains on your data, paid tier does not | no Romanian guarantee |

**Recommendation:** Gemini 3.8 Live first (Romanian guaranteed, async tools fit a slow brain, cheapest); gpt-realtime-2
as the second adapter for an A/B by ear on the same Romanian script, and because it can use a custom voice. The
decision is made by Laris listening, not by a table. Deepgram Voice Agent API (STT + our tool + Flux TTS in one
socket) was considered: it fixes the walkie-talkie turn-taking but is still text-to-speech, so it does not answer the
"emotion" complaint; the existing Deepgram path is kept as the fallback instead.

## 5. Decisions made here (architect's calls, change them if you disagree)

1. Server proxy with an adapter interface (above).
2. One tool, `ask_goat`; the voice model never calls GOAT's own tools.
3. Phase 1-3 are half-duplex with **tap to interrupt**: the page does not send microphone audio while it is playing
   the model's voice (it is the only one that knows what is still playing, so there is no echo), and one tap cuts the
   model off and reopens the microphone. Talking over the model by voice
   (barge-in with echo cancellation) is its own phase because it can fail on phones.
4. GOAT receives the model's `request` argument (not the transcript); the session is **seeded with the recent chat
   history** at connect so follow-ups ("ce ai zis mai devreme?") work. The GOAT turn is never cancelled
   from the voice side (it may be doing something real). Normal results go back with `WHEN_IDLE` scheduling so the model
   never talks over the person; if the provider withdraws the call (`toolCallCancellation`) the result goes back
   `SILENT`, so the model knows it without saying it.
5. Session caps: 15 min with resumption, 90 s of silence ends it, a hard daily-minutes counter is shown on the Dashboard.

## 6. Risks

- **Emotion and Romanian quality** are only judged by ear: the first listening sample is a phase gate, not a footnote.
- **Echo / barge-in on phones**: an open microphone with echo cancellation makes phones play everything at the quiet
  call volume and may route it to the earpiece (found in #41, which therefore releases the microphone while GOAT
  speaks). Talking over the model by voice may simply not be possible on a phone; tap-to-interrupt is then the design
  there, and the page releases the microphone while the model's voice plays (it reopens it right after).
- **Latency of GOAT's turn** (5-60 s): mitigated by non-blocking tools and fillers, but the model must not talk over
  itself or invent a result while waiting.
- **Voice model drift:** it may answer from its own head instead of asking GOAT; the prompt and an eval list of
  "must call ask_goat" phrases guard this.
- **Preview APIs** change names and limits: everything provider-specific stays in one adapter.
- **Ambient speech** (TV, other people) becomes GOAT input; the same guardrails as typed text apply, and destructive
  actions keep their existing confirmations.

## 7. Open questions for Laris

0. **Listen first (ten minutes, no code):** talk Romanian with both models in their own playgrounds, Google AI Studio
   "Stream" with `gemini-3.8-live` and OpenAI's Realtime playground with `gpt-realtime-2`, and say which one sounds
   real and emotional. It may also settle question 1.

1. "Vocea lui reală": a natural, expressive voice for GOAT (assumed), or **Laris's own cloned voice**? Cloning needs a
   consent recording; I found it documented only for OpenAI; it is not part of the first phases.
2. OK to send the live audio and GOAT's replies to Google (and later OpenAI)? Deepgram already hears the audio today.
   Only the **paid** Gemini tier keeps data out of training.
3. Keys: a Gemini API key (AI Studio, paid tier) for phase 2; an OpenAI key for phase 3.
