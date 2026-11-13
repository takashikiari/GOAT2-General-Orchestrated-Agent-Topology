# Voice live through GOAT's memory pipeline — design

**Date:** 2026-11-13 · **Status:** spec, awaiting Laris's OK (via GOAT) · **Decision:** option B (GOAT, 2026-11-13 09:09)
**Base:** main `378f99d` · **Todo:** T30

## Goal

Laris: the live voice (Gemini Live) must go *integrally* through GOAT's pipeline, not beside it. GOAT's decision is
option B: the voice model stays conversational, so the conversation stays fluid. Two things close the gaps, and
neither of them moves every reply into `orchestrator.run()`:

1. **In**: the voice starts with GOAT's memory and keeps it current.
2. **Out**: every exchange is written to memory as a normal turn.

Option A (GOAT writes every spoken reply) is out of scope. Laris rejected turn-by-turn on 2026-11-10 as not fluid.

## Where the chain breaks today (read-only findings, main 378f99d)

| # | Gap | Where |
|---|-----|-------|
| R1 | The voice model answers greetings and small talk itself, with no thread state, no L1/L2/L3 and no AITS. | `admin_panel/voice_live/prompt.py:39-40`, `:42-45` |
| R2 | The voice model's context is the system prompt plus the last 12 L2 messages (600 chars each), sent once at connect. It gets no L1, no L3 and no refresh during the call. | `voice_live/bridge.py:89-111`, `session.py:214` |
| R3 | What the voice actually says is sent to the page only. It is never stored. | `voice_live/session.py:99-101` |
| R4 | Laris's words reach memory late and bundled. They are glued to the next `ask_goat` request (`with_words`), and the rest goes out as one end-of-call turn (`end_note`). In L3 they carry the request's time, not the time he spoke, mixed with the model's paraphrase, and are truncated to 6000 chars. | `voice_live/transcript.py:30-67`, `session.py:237-239` |
| R5 | The end note is a full GOAT turn (LLM reply) that nobody hears. It is mirrored to Telegram, costs tokens and adds a message after every call. | `voice_live/bridge.py:56-64`, `orchestrator/chat_service.py:132` |
| R6 | Nothing marks a turn as spoken. Voice and typed turns are indistinguishable in memory. | — |

## Design

### 1. Store-only turn (the new piece)

A way to write one user/assistant pair into memory exactly as a normal GOAT turn does, but without producing a
reply. Same L2 format, same L3 archive path, same chunk and enrichment side effects. No LLM call, no AITS, no
Telegram mirror, no tool loop.

- New `orchestrator.voice_memory.store_exchange(layers, chat_id, user_text, assistant_text, *, user_ts, assistant_ts,
  lane="human", tags=("voice_live",)) -> str` (returns the L3 doc id). Its steps:
  - L2: `layers.append_and_save_working_context(chat_id, {"role": "user", "content": user_text, "timestamp": user_ts,
    "l3_id": doc_id, "source": "voice_live"}, {"role": "assistant", "content": assistant_text, "timestamp":
    assistant_ts, "l3_id": doc_id, "source": "voice_live"})`. This is the same call as `orchestrator.py:626-630`, so
    auto-promote and its cache clear run exactly as after a typed turn.
  - L3: reuse `_archive_turn` (`orchestrator.py:178`) with `topic_id` = the human lane's current `activation.topic_id`
    (empty if there is none) and the pre-generated `doc_id`. Content stays `user: …\nassistant: …` (`:194`), verbatim.
    **Change needed:** `_archive_turn` and `layers.store_episodic` get an optional `timestamp` (default:
    `time.time()`, today's behaviour, `memory/layers.py:193`), so the L3 entry carries `user_ts`. The time Laris
    spoke is the time memory shows, not the time the write happened.
  - Tags: `l2_full_archive` (as today) plus `voice_live`, so a spoken exchange can be filtered or measured.
  - Runs under the chat's per-chat lock (`_chat_locks`, the lock `ChatService` already holds). A store-only write
    never interleaves with a typed or Telegram turn's read-modify-write.
- **Exposed via `ChatService.store(user_text, assistant_text, user_ts, assistant_ts)`.** The voice route already only
  holds a `ChatService` (`routes/voice_live.py:41`). `ChatService` knows the admin chat id and the locks, and does
  NOT mirror store-only turns to Telegram.
- Thread state: v1 does **not** classify the turn or update the human activation. That needs an embedding and
  `classify_turn` per utterance; see Open question 1.

### 2. What becomes an exchange

The session pairs what Laris said with what the voice said back:

- `UserText(final=True)` starts or extends the pending user text, timestamped at its first final transcript.
- `AssistantText` accumulates into the pending assistant text, timestamped at its first piece.
- On `TurnComplete` (or `Interrupted`), the pair is flushed through `ChatService.store`, fire-and-forget, logged on
  failure. The session never blocks on memory.
- An exchange that contains an `ask_goat` call is **not** stored again. The request already goes through
  `orchestrator.run()` as a normal turn (with Laris's words; see 3), so storing it twice would duplicate it in L2/L3.
  The voice's spoken summary of GOAT's answer is dropped for that exchange (GOAT's full answer is the stored one).
- A user text with no reply (the call ends mid-sentence) is flushed with an empty assistant side at session end.
  That replaces `end_note`.

### 3. `ask_goat` keeps Laris's words, without `with_words`

`ask_goat` stays the road for actions, tools and hard questions (`prompt.py:42-45` unchanged). The request GOAT
receives is the voice model's self-contained wording, which is what GOAT should act on. Laris's verbatim words of
that exchange are stored **as the user side** of the L2/L3 pair instead of the paraphrase. `ChatService.submit` gets
an optional `stored_text` (default: the text itself). The orchestrator answers the request, but `_archive_turn` and
L2 store `stored_text`. `with_words` and `restore` disappear. `Heard` shrinks to the per-exchange pending buffer.

### 4. Context in: memory at connect, refresh on topic change

- **At connect** (`routes/voice_live.py`, replacing `recent_history`'s role as the only context), the voice model
  gets:
  - L1: `layers.get_identity_and_facts()` (`memory/layers.py:81`), capped at `l1_facts_max_tokens`;
  - L3: the human lane's held activation (`activation.merged`, re-scored with `rescore_recency`) if there is one;
    otherwise one `retrieve(..., state="cold")` on the last L2 user message;
  - L2: the last 12 messages as today (`bridge.py:89-111`).

  All of it goes in one context-only block. For Gemini it uses the existing
  `{"clientContent": {"turns": …, "turnComplete": false}}` path (`voice_live/gemini.py:52`); for OpenAI it uses the
  existing history items in `session.update`.
- **Refresh**: a new `Provider.send_context(text)` with the same context-only, no-reply semantics. Gemini uses
  `clientContent`, `turnComplete: false`; OpenAI uses `conversation.item.create` (a system message, no
  `response.create`). After each stored exchange, the session embeds the exchange's user text and compares it with
  the centroid of the context last sent (`memory.activation.cosine`). Below `drift_cold` (0.30), it runs one
  `retrieve(..., "drift")` and sends the new L3 slice. At most one refresh in flight, at most one per N seconds
  (config, default 30). No activation write: the human lane's thread state stays owned by real GOAT turns (Open
  question 1).
- Budget: the context block is capped by tokens (config, default 2000). L3 entries are packed with the existing
  `memory/l3_pack.py`.

### 5. Removed

- `Heard.with_words`, `Heard.restore`, `Heard.end_note` and `GoatBridge.note`. R5 goes away: no LLM turn and no
  Telegram message after a call.
- The comment contract in `voice_live/transcript.py` is rewritten to describe pairs.

### 6. Config (`config/memory.toml` or `voice` config, new keys)

`[voice_live] store_exchanges = true` (rollback: false, which also disables refresh), `context_tokens = 2000`,
`refresh_min_seconds = 30`.

## Observability

- One log line per stored exchange: `voice_live: stored exchange chat=… doc=… user_chars=… assistant_chars=…`.
- One per refresh: `voice_live: context refresh cosine=… l3=…`.
- The existing `voice_live: Laris said (transcript)` log stays, to compare what was said with what was stored.

## Tests

- `store_exchange`:
  - L2 gets two messages with the given timestamps, `l3_id` and `source=voice_live`;
  - `_archive_turn` is called with verbatim content, the `voice_live` tag, the human topic id and `timestamp=user_ts`;
  - no LLM call;
  - no mirror;
  - it holds the per-chat lock.
- `store_episodic(timestamp=…)`: metadata `timestamp` equals the given one; the default is unchanged.
- Session pairing:
  - final user texts and assistant texts flushed on `TurnComplete`;
  - an exchange with `ask_goat` is not double-stored;
  - an unanswered user text is flushed at the end;
  - a store failure does not break the session.
- `ask_goat`: GOAT receives the model's request, but L2/L3 contain Laris's words (`stored_text`).
- Connect context: L1 + L3 (held activation, or a cold retrieve) + L2 in one context-only block, within the token cap.
- Refresh:
  - fires only below `drift_cold`;
  - rate-limited;
  - never writes activation;
  - `send_context` sends no reply request on both providers (fake provider in `voice_live/fake.py`).
- Regression: typed panel turns and Telegram turns are unchanged (existing suites).

## Open questions (for Laris / GOAT before code)

1. Should spoken exchanges also move the human thread (embed + `classify_turn` + `update_activation` on the human
   lane), so the next typed message continues from what was said aloud? This is cheap (one embedding per exchange),
   but it lets voice small talk drift the typed thread. Proposed for v1: **no**; measure first.
2. `ask_goat` exchanges: drop the voice's spoken summary (proposed), or store it too as a second, assistant-only L2
   line?
3. Laris hears the voice's replies, so they are "what GOAT said" from his side. Should they be stored under the
   assistant role (proposed), or a distinct `voice` role that the context assembler shows differently?
