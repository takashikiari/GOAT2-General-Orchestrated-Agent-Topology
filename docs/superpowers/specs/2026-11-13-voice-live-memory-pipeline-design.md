# Voice live = the orchestrator's voice — design

**Date:** 2026-11-13 · **Status:** spec v2, awaiting GOAT's approval (no code before it) · **Todo:** T30
**Decision (Laris, via GOAT, 2026-11-13 09:13):** option B (a conversational voice beside GOAT) is rejected. The live
voice is the voice of the orchestrator. Every spoken turn goes through `Orchestrator.run` (LLM + tools + memory), and
the orchestrator's answer is what is heard. Gemini stays the ears and the mouth only. Latency is accepted; it is not
an argument.
**Base:** main `378f99d`.

## Today (read-only findings, main 378f99d) and what changes

| # | Today | Where | In this design |
|---|-------|-------|----------------|
| R1 | The voice model answers greetings, thanks, small talk and acknowledgements itself. Only the rest goes through `ask_goat`. | `voice_live/prompt.py:39-45` | Gone. Every final utterance goes to the orchestrator; the model never answers from itself. |
| R2 | The model's context is the system prompt plus the last 12 L2 messages, given once at connect. | `voice_live/bridge.py:89-111`, `session.py:214` | No longer needed for answering (see §6). |
| R3 | What the voice says is sent to the page only and never stored. | `voice_live/session.py:99-101` | What is said IS the orchestrator's reply, stored by `run()` as for any turn. The output transcription is kept only to measure fidelity (§3). |
| R4 | Laris's words are glued to the next `ask_goat` (`with_words`), and the leftovers go out as an end note. | `voice_live/transcript.py:30-67`, `session.py:237-239` | Gone. Each utterance is its own turn, with his words verbatim as the user's side. |
| R5 | The end note is an extra full GOAT turn that nobody hears. It is mirrored to Telegram. | `voice_live/bridge.py:56-64` | Gone. There is no unanswered text. |
| R6 | Nothing marks a turn as spoken. | — | `voice_live` tag in L3 and `source` in L2. |

Today `ask_goat` is a NON_BLOCKING tool (`voice_live/gemini.py:133`): the model keeps talking while GOAT works. In
this design it must not.

## Design

### 1. Every final utterance becomes one orchestrator turn

- The trigger is the provider's final input transcription (`UserText(final=True)`), i.e. Laris's own words.
- **Model-driven, with a guard (proposed):**
  - The prompt and the session config make `ask_goat` the only thing the model may do: every user turn produces
    one `ask_goat` call. The `request` argument is ignored; the session uses Laris's final transcription as the
    turn's text.
  - Forcing the call:
    - OpenAI Realtime: `tool_choice: "required"` in `session.update`.
    - Gemini Live: `toolConfig.functionCallingConfig.mode = "ANY"` if the Live API honours it. This is unverified
      and is the first spike item (§8).
  - `ask_goat` becomes **blocking** on Gemini (drop `"behavior": "NON_BLOCKING"`). The model waits for the result
    instead of talking over GOAT's work.
- **Guard (both providers).** If the model starts a spoken reply for a user turn without calling `ask_goat`
  (`Audio`/`AssistantText` after a final `UserText` and no `ToolCall`), the session:
  - drops that audio (the page never plays it; the existing discard path `gemini.py` `_discarding` /
    `provider.interrupt()`);
  - submits the transcription itself;
  - delivers the answer as in §3.

  So nothing the model invents is heard, even if the prompt is not obeyed.
- The turn runs through `ChatService.submit(text=<transcription>, source="voice_live", said_at=<ts of the final
  transcript>)`. That means the same `Orchestrator.run`, the same per-chat lock and the same lane human as the
  Telegram and panel chat.

### 2. The orchestrator answers for the ear

`Orchestrator.run(..., voice=True)` adds one instruction to the turn's system prompt: answer as speech. That means
Romanian, plain sentences, no markdown, lists, code, links or long numbers, short by default, and an offer of details
in the chat when they don't fit in speech. Everything else is the normal turn: tools, memory and AITS. The reply text
is stored and mirrored like any turn (§5).

### 3. Delivering the answer: toolResponse + "say exactly this" (proposed)

The three options:

- **(a) toolResponse.** The answer goes back as the `ask_goat` result: `{"say": <reply>}`. The prompt and tool
  description say: speak the `say` text exactly, word for word, add nothing before or after, never summarize.
  - This fits the function-call protocol the model is already in, and it is one audio path.
  - Barge-in keeps working through the provider's own VAD (`Interrupted`) and the voice stays the same.
  - The answer is spoken as soon as the result arrives (blocking tool: the model is waiting for it).
- **(b) As context, then let the model generate.** Send the reply as `clientContent` (or a system item) and let the
  model speak "its own version". **Rejected.** The model rewrites freely, so what is heard is no longer the
  orchestrator's answer, which is the very thing Laris asked to stop.
- **(c) Text to a separate TTS.** For example the existing Deepgram TTS (`routes/voice_speak.py`), or a Gemini TTS
  model.
  - Exact words are guaranteed.
  - But it means a second audio path beside the Live session, with its own playback, barge-in and cancel logic,
    another vendor call per turn, and a different voice from the one listening.
  - Laris judged the Deepgram turn-by-turn voice poor (2026-11-10).
  - **Fallback only**, if (a)'s fidelity measures badly.

**Fidelity is measured, not assumed.** Both providers return the output transcription (`AssistantText`), so for every
turn the session compares what was spoken with the reply text. It logs
`voice_live: spoken fidelity turn=… ratio=… extra_words=…` (a word-level similarity, e.g. `difflib.SequenceMatcher`).
If the median ratio stays under a threshold (config, default 0.9) over real use, we switch to (c). Answers are
already short (§2), which keeps verbatim reading reliable.

### 4. What is heard while the orchestrator thinks

No words.

- The model is told to stay silent while `ask_goat` runs: no "o secundă", no filler. The current prompt line "Say one
  short natural sentence first" (`prompt.py`, "While GOAT works") is removed. A blocking tool on Gemini and a
  required tool on OpenAI also mean the model has nothing to say until the result comes.
- **The page plays a short, soft earcon** (a single low tone, ~150 ms, client-side, never sent to the model) when the
  state turns `thinking`. The session already sends `{"type": "state", "state": "thinking"}`, and the page already
  has the phase (`frontend/src/voice/useLiveConversation.ts:233`). If thinking lasts beyond a threshold (config,
  default 8 s), it plays a quieter repeat every 8 s, so silence never reads as a dropped call. The visual
  "thinking" pulse stays.
- Tool progress (`tool` running/done events, already forwarded by `GoatBridge._wait`) stays visual only.

### 5. Memory: a normal turn, with the real time and a tag

- Because each utterance is a normal `Orchestrator.run` turn, L2 and L3 are written by the existing save path
  (`orchestrator.py:625-636`). The user's side is Laris's transcription verbatim; the assistant's side is the
  orchestrator's reply (what was spoken).
- **Real time.** A spoken turn can wait behind another turn (the lock), so its write time can be later than when it
  was said. `said_at` flows `ChatService.submit` → `run(..., said_at=)` → the L2 message `timestamp` and
  `_archive_turn(..., timestamp=)` → `layers.store_episodic(..., timestamp=)`. Today the latter is `time.time()` at
  write, `memory/layers.py:193`; it becomes optional, with the default unchanged.
- **Tag.** `source="voice_live"` → L3 tags `l2_full_archive,voice_live` and L2 `"source": "voice_live"`, so spoken
  turns can be filtered and measured.
- **Lane human.** It is Laris's conversation; the activation and classify_turn run as for a typed turn.
- **No separate store-only path is needed.** Every utterance gets an orchestrator turn, so nothing is left without
  a reply to store on its own. The option-B `store_exchange` is dropped (YAGNI). If a turn is still queued when the
  call ends, it runs to completion in the background (as `GoatBridge` does today) and is stored by `run()` itself.

### 6. Context at connect

The model no longer answers, so the L1/L3 context it would have needed is not needed for answering; the orchestrator
has the full memory. What it needs is to hear Laris well. The connect payload becomes:

- the system prompt (§7);
- the last few L2 messages, kept short (today 12, kept), only so names and topics are recognised in transcription
  and the reading flows.

L1 + L3 at connect and mid-call refresh (spec v1 §4) are **dropped**: no answer depends on them, and giving the
model memory invites it to answer by itself.

### 7. Prompt (`voice_live/prompt.py`) rewritten

Role: you are only the ears and the voice of GOAT. Rules:

1. For everything Laris says, call `ask_goat` and say nothing yourself.
2. While it runs, stay silent.
3. When the result comes, speak the `say` text exactly, in Romanian, word for word, nothing before or after.
4. If the audio was unclear, still call `ask_goat` with what you heard. GOAT asks back if needed.

The sections "What you answer yourself" and "While GOAT works" go away. The `ask_goat` description and its
`request` parameter stay for compatibility; the session uses the transcription.

### 8. Spike before code (real APIs, a few minutes, small cost; only with Laris's OK)

1. Gemini Live: does `toolConfig.functionCallingConfig.mode = "ANY"` force a call on every turn? Does a blocking
   tool keep the model silent until the response? Does it speak `say` verbatim (fidelity ratio on 10 answers)?
2. OpenAI Realtime: the same with `tool_choice: "required"`.
3. Barge-in during the spoken answer and during thinking (§9).

The results decide the guard's role (a rarely-firing safety net, or the main path) and confirm (a) vs (c).

### 9. Turn-taking

- **Interrupting the spoken answer:** the provider's VAD cuts playback (`Interrupted`), as today. The rest of the
  answer stays in the chat. The new utterance becomes the next turn.
- **Speaking while GOAT thinks:** the new final utterance is queued behind the running turn (the per-chat lock;
  `ChatService` max_queue 3). Utterances that arrive while one is still *queued* (not started) are merged into that
  queued turn, joined with a space, so a sentence split by a pause is one turn, not two.
- GOAT cannot interrupt Laris (he speaks, it listens). Being interrupted is covered by the VAD.

## Removed

`Heard.with_words`, `Heard.restore`, `Heard.end_note`, `GoatBridge.note`, the end-note block in
`session.run_session`, the NON_BLOCKING behaviour on `ask_goat`, and the prompt sections
"What you answer yourself" / "While GOAT works".

## Config (new keys)

`[voice_live] fidelity_threshold = 0.9`, `thinking_earcon_repeat_seconds = 8`. The guard is always on (no switch:
nothing the model makes up may be heard).

## Tests

- Session:
  - a final `UserText` leads to exactly one `ChatService.submit` with the transcription, `source=voice_live` and
    `said_at`;
  - the model's `request` argument is not used as the text;
  - an utterance arriving while a turn is queued merges into it;
  - while running, it is queued.
- Guard: audio or text from the model with no `ask_goat` after a final utterance is not forwarded to the page, and
  the transcription is submitted by the session.
- Delivery: the tool result carries `{"say": reply}`. On Gemini the tool is blocking (no `behavior` field); on OpenAI
  `tool_choice` is `required`.
- Fidelity: the log line ratio equals the computed similarity for known reply/transcript pairs.
- Memory: an L2 message with the `said_at` timestamp and `source`; `_archive_turn` with the `voice_live` tag and
  `timestamp=said_at`; `store_episodic(timestamp=)` sets the metadata and date prefix, with the default unchanged.
- Orchestrator `voice=True`: the speech instruction is in the system prompt; it is absent by default.
- Removed paths: no end note and no `with_words` (session end submits nothing).
- Page: the earcon on `thinking`, the repeat after the threshold, and nothing sent to the model.
- Regression: Telegram, panel typed chat and the Deepgram voice are unchanged.

## Open questions (replacing v1's a/b/c)

1. **Telegram copy of spoken turns.** Every utterance, including "da" and "mersi", becomes a Telegram message pair
   (one conversation, as today for the panel). Keep mirroring all of them (proposed: yes, one conversation), or
   mirror only turns that used tools?
2. **Answer length.** Short spoken answers with "details in the chat" (proposed), or speak the full answer
   whatever its length?
3. **The Deepgram voice** (`voice_stream` / `voice_speak`, turn-by-turn, already orchestrator-driven): keep it as is
   beside the live voice, or retire it once this works?
4. **Spike (§8):** OK to spend a few minutes of real Gemini/OpenAI Live calls to verify forced tool calls and
   verbatim fidelity before coding?
