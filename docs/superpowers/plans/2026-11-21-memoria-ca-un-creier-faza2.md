# Memoria ca un creier — faza 2 (bucla de bază) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** GOAT's memory works as one loop: every turn is understood (classified, connected) when it enters, the message's cues light one shared working memory, the prompt is built from that working memory (what is known, what happened, what is open, what is missing), and GOAT no longer searches memory with tools.

**Architecture:** A new working-memory store (`brain/mind.py`, in `brain.sqlite`) is the single "În minte" state per chat and lane. One LLM step (`brain/understand.py`) classifies both incoming messages (cues) and finished turns (encoding). `brain/encode.py` writes the result into the existing graph as typed nodes and links. `brain/mind_live.py` runs the turn: expose before the reply, learn and encode after it. The orchestrator calls it under one switch, `[mind] enabled`; off = exactly today's behaviour.

**Tech Stack:** Python 3.12, sqlite3 (`brain.sqlite`), the existing AsyncOpenAI-compatible client (`registry.llm_client`, `settings.MODEL_NAME`), pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-11-21-in-minte-design.md` (approved by Laris 2026-11-21). This plan covers spec §6 phase 2: F1, F2, F4, F7 and §3 „Fără cârje". F3, F5, F6 and the clean-up (§4 „ies") are later plans.

## Global Constraints

- Every file ≤ 260 lines, one responsibility (`orchestrator/orchestrator.py` is already over: add at most a few call lines to it, logic goes in new modules).
- **Nothing is ever deleted** from the graph or the working memory: no `DELETE`/`DROP`/`TRUNCATE` in `brain/`; a test greps for it (like `tests/test_brain_graph.py::test_there_is_no_delete_anywhere_in_the_graph_code`). Fading goes to a floor of `0.001`.
- `[mind] enabled = false` must leave every prompt, tool list and background task exactly as on main `1198903`.
- The LLM step uses `registry.llm_client` with `settings.MODEL_NAME`, `max_tokens >= 4000` (deepseek reasoning tokens count against it; an empty reply with `finish_reason=length` means the cap is too low). Cost and latency are not criteria (Laris).
- All prompt text GOAT sees is Romanian.
- Agent-coord messages (lane `channel`) are encoded and recalled like any lived conversation; `brain/live.py::_not_lived` must not exclude them when `[mind]` is on.
- No new measurement pipelines. Errors are logged as warnings and the turn goes on without the new memory; one INFO line per turn at most.
- Typed connection kinds (exact strings): `continuă`, `cauză`, `urmare`, `corectează`, `contrazice`; item kinds in the working memory: `amintire`, `fapt`, `deschis`; turn types: `fapt`, `eveniment`, `decizie`, `promisiune`, `preferință`, `întrebare`, `corectură`.

## Review Focus

1. The LLM returns invalid JSON, an empty reply or times out → the reply still goes out; the turn's encoding is left unencoded and the sleep backfill (Task 7) picks it up later.
2. A short message with no cues ("ok", "🤣") → no "nimic despre" line, the carried-over working memory is still shown.
3. The LLM names a thing that exists under the same normalized label → it links to the existing node (`brain.graph.norm_thing`), never to a duplicate.
4. `[mind] enabled = false` → the assembled system prompt and the offered tool names are byte-identical to today.
5. An open item (a promise or an unanswered question) stays in the working memory across turns until a later turn closes it, and closing keeps the row.

---

### Task 1: Switch and working-memory store

**Files:**
- Create: `brain/mind.py`
- Modify: `config/brain.toml` (new `[mind]` section), `brain/brain.py` (read `[mind]` into a `MIND` dict next to `CONFIG`)
- Test: `tests/test_brain_mind.py`

**Interfaces:**
- Produces: `MIND: dict` in `brain/brain.py` with keys `enabled` (False), `carry` (0.6), `floor` (0.001), `expose_items` (12), `encode_per_tick` (20); `mind_on() -> bool` (= `is_enabled() and MIND["enabled"]`).
- Produces in `brain/mind.py`: `SCHEMA` (tables `mind(chat_id, lane, item, kind, activation, sources, why, updated, last_used, PRIMARY KEY(chat_id, lane, item))`, `episode_meta(id PK, type, importance, gist, encoded_at)`, `open_items(id PK, chat_id, lane, kind, text, ts, closed_ts, closed_by)`); `carry(db, chat_id, lane, factor, floor) -> int`; `light(db, chat_id, lane, item, kind, amount, source, why, now) -> float` (activation = min(1, current + amount), source appended once); `top(db, chat_id, lane, limit, min_activation) -> list[dict]`; `mark(db, chat_id, lane, used: dict[str, bool], now) -> None` (used +0.2, present-only ×0.8, never below floor).

- [ ] **Step 1: Write the failing tests** — `test_light_converges_and_caps_at_one` (two sources of 0.6 → 1.0, `sources == ["search", "graph"]`), `test_carry_fades_to_floor_and_keeps_the_row` (10 carries of 0.6 → activation 0.001, row count unchanged), `test_top_orders_by_activation_and_filters_by_lane`, `test_mark_reinforces_used_and_fades_present`, `test_no_delete_in_brain_code` (grep over `brain/*.py`), `test_mind_off_by_default`.
- [ ] **Step 2:** `python3 -m pytest tests/test_brain_mind.py -q` → FAIL (module missing).
- [ ] **Step 3:** implement; `brain/store.py` runs `executescript(mind.SCHEMA)` next to `graph.SCHEMA`.
- [ ] **Step 4:** `python3 -m pytest tests/test_brain_mind.py tests/test_brain_graph.py -q` → PASS.
- [ ] **Step 5:** commit `brain: working memory store and [mind] switch (off)`.

### Task 2: Understanding (the LLM classification step)

**Files:**
- Create: `brain/understand.py`
- Test: `tests/test_brain_understand.py`

**Interfaces:**
- Consumes: `registry.llm_client` (AsyncOpenAI), `settings.MODEL_NAME`.
- Produces: `async understand_turn(client, user: str, reply: str, in_mind: list[dict]) -> dict | None` returning `{"type", "importance" (0..1), "gist" (one Romanian sentence), "about": [labels], "links": [{"to": item id from in_mind, "kind": one of the 5 kinds}], "opens": {"kind": "promisiune"|"întrebare", "text"} | None, "closes": [open item ids]}`; `async understand_message(client, text: str, in_mind: list[dict]) -> dict | None` returning `{"about": [labels], "asks_past": bool}`. `None` on any failure.
- The prompt (Romanian, constant in the module) lists `in_mind` items as `id — kind — gist/text (≤160 chars)` so the model can only link to ids it was shown; ids not in `in_mind` and unknown kinds are dropped after parsing.

- [ ] **Step 1: Write the failing tests** with a fake client returning canned `choices[0].message.content`: `test_turn_is_classified_and_links_only_to_shown_ids` (a link to an unknown id and a kind `"vecin"` are dropped), `test_json_inside_code_fence_is_parsed`, `test_invalid_or_empty_reply_returns_none` (also `finish_reason="length"` with empty content), `test_message_cues`, `test_request_uses_model_name_and_max_tokens_4000`.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** implement; JSON is the first `{...}` block of the content; `response_format` not required.
- [ ] **Step 4:** run → PASS.
- [ ] **Step 5:** commit `brain: understanding step (classify turns and message cues)`.

### Task 3: Encoding into the graph

**Files:**
- Create: `brain/encode.py`
- Test: `tests/test_brain_encode.py`

**Interfaces:**
- Consumes: Task 2 dict shape; `brain.graph.norm_thing`, `brain.graph.touch`, `brain.mind` tables.
- Produces: `encode(db, chat_id, lane, episode_id, ts, u: dict, now) -> dict` (counts: `things`, `links`, `opened`, `closed`). Writes: episode node (`INSERT OR IGNORE`, same columns as `brain.graph.ingest`), `despre` to the thing for each `about` label (normalized with `norm_thing(label, "project")`; an existing node is reused), each typed link as an edge episode→`to` of that kind via `graph.touch(..., initial=0.5)`, `episode_meta` row, an `open_items` row for `opens`, `closed_ts/closed_by` set for each id in `closes`.

- [ ] **Step 1: Write the failing tests:** `test_about_reuses_existing_thing_node`, `test_typed_links_are_edges_of_that_kind`, `test_open_then_close_keeps_the_row`, `test_reencoding_the_same_turn_is_idempotent` (node/edge counts unchanged), `test_counts_never_decrease`.
- [ ] **Step 2:** run → FAIL. **Step 3:** implement. **Step 4:** run → PASS.
- [ ] **Step 5:** commit `brain: encode understood turns as typed graph links, meta and open items`.

### Task 4: Exposure (cues light the working memory; the prompt block)

**Files:**
- Create: `brain/mind_live.py` (expose part)
- Test: `tests/test_brain_mind_expose.py`

**Interfaces:**
- Consumes: Tasks 1-3; `brain.recall.spread(db, seeds, now, limit, hops, half_life_days)`; L1 facts dict (`layers.get_identity_and_facts()` result); search results (`l3_results`, each with `metadata.message_id`, `blended_score`, `content`); an episodic store with `_get_collection().get(ids=..., include=["documents","metadatas"])`.
- Produces: `async expose(client, episodic, chat_id, lane, message, l3_results, facts, budget_tokens) -> tuple[str, list[str]]` → (the Romanian block, ids of the `amintire` items shown). Steps inside, in this order: `carry`; light each search result by its `blended_score` (source `căutare`); `understand_message` → things for `about` (missing ones remembered for F7); spread from those things plus every item now above `floor` (source `asociere`, amount = activation); open items not closed (kind `deschis`, amount 1.0); `top(expose_items)`; render within `budget_tokens`.
- Block format (exact headers): `[În minte]`, then `Ce știu:` (every L1 fact, `- cheie: valoare`), `Ce s-a întâmplat:` (per amintire: `- (data) gist — de ce`, plus the raw text for the 3 most active, ≤600 chars each), `Ce e deschis:`, `Ce nu știu:` (`- nu ai nimic despre X` for each missing cue, only when `asks_past` or the cue is a person/thing not in the graph).

- [ ] **Step 1: Write the failing tests** (fake client, in-memory brain, fake episodic): `test_association_reaches_the_prompt_without_text_similarity` (an episode linked by `corectează` to a lit thing appears though search did not return it), `test_convergence_puts_search_and_graph_hits_first`, `test_open_items_are_shown_until_closed`, `test_missing_cue_is_said_and_short_messages_say_nothing` (Review Focus 2), `test_block_fits_the_budget`, `test_understanding_failure_falls_back_to_search_and_carry` (Review Focus 1).
- [ ] **Step 2:** run → FAIL. **Step 3:** implement. **Step 4:** run → PASS.
- [ ] **Step 5:** commit `brain: expose the working memory as one prompt block`.

### Task 5: After the reply (learn and encode)

**Files:**
- Modify: `brain/mind_live.py` (add `after_turn`)
- Test: `tests/test_brain_mind_after.py`

**Interfaces:**
- Consumes: `brain.live.used(text, reply_terms)` and `memory.usage_probe._terms`; Tasks 1-4.
- Produces: `async after_turn(client, chat_id, lane, turn_id, user, reply, shown: list[str], texts: dict[str, str]) -> None` — `mark` (used/present), `gândite_împreună` turn↔shown (0.2 used / 0.05 present, as `brain/live.py` today), then `understand_turn` with the current `top()` as `in_mind` and `encode`; on understanding failure the turn stays unencoded (no `episode_meta` row) for Task 7.

- [ ] **Step 1: Write the failing tests:** `test_used_memories_stay_lit_and_link_to_the_turn`, `test_turn_is_encoded_with_links_to_what_was_in_mind`, `test_failed_understanding_leaves_turn_for_backfill`.
- [ ] **Step 2:** run → FAIL. **Step 3:** implement. **Step 4:** run → PASS.
- [ ] **Step 5:** commit `brain: after the reply, reinforce what was used and encode the turn`.

### Task 6: Orchestrator wiring and no crutches

**Files:**
- Create: `orchestrator/mind_hook.py` (≤ 80 lines: the switch-dependent pieces)
- Modify: `orchestrator/orchestrator.py` (only call lines at the recall/assemble step ~505-519, the guidance/nucleus step ~530-535, the tools list ~320, the after-turn task ~712), `brain/live.py::_not_lived` (returns False when `mind_on()`)
- Test: `tests/test_orchestrator_mind.py`

**Interfaces:**
- Consumes: `expose`, `after_turn` (Tasks 4-5).
- Produces in `mind_hook.py`: `MIND_TOOLS_HIDDEN = {"search_memory", "read_l1"}`; `MIND_GUIDANCE` (Romanian: the `[În minte]` block is your memory; if something is not there, say you don't remember it; never search memory with tools, shell or files); `hidden_tools() -> set[str]` (= `MIND_TOOLS_HIDDEN` when on, else `set()`); `async assemble(...)` that, when on, calls `layers.assemble_context` with `l3_results=[]` and `facts={}` (the L1 facts go under `Ce știu`, not twice), computes the leftover budget from the returned blocks and appends the `expose` block. When on: no `_SEARCH_MEMORY_GUIDANCE`, no nucleus block, no `tool_gate` hiding, no `_brain_live.recall_into`/`after_turn` (replaced).

- [ ] **Step 1: Write the failing tests** with the fakes in `tests/_orch_fakes.py`: `test_off_prompt_and_tools_unchanged` (Review Focus 4: system content and tool names equal to a run with the module absent), `test_on_prompt_has_one_memory_block_and_no_search_tools`, `test_on_after_turn_is_scheduled_with_shown_ids`, `test_channel_lane_is_lived_when_on` (Review Focus 5 part).
- [ ] **Step 2:** run → FAIL. **Step 3:** implement. **Step 4:** `python3 -m pytest tests/ -q` → all pass (1118 + new).
- [ ] **Step 5:** commit `orchestrator: build the prompt from the working memory under [mind]; no memory tools`.

### Task 7: Sleep backfill (history goes through the same encoding)

**Files:**
- Create: `brain/mind_sleep.py`
- Modify: `telegram_interface/_brain_sleep.py` (one call on the existing tick, only when `mind_on()`)
- Test: `tests/test_brain_mind_sleep.py`

**Interfaces:**
- Produces: `async encode_slice(brain, episodic, client, limit) -> int` — the oldest L3 episodes without an `episode_meta` row (all lanes, including `channel`), each understood with the previous episodes of the same topic (≤ 8, by `graph_nodes.topic_id`/`ts`) as `in_mind`, then `encode`. Returns how many were encoded; runs under `brain.lock` only for the writes.

- [ ] **Step 1: Write the failing tests:** `test_backfill_encodes_oldest_first_and_links_within_topic`, `test_backfill_skips_encoded_and_retries_failed_later`.
- [ ] **Step 2:** run → FAIL. **Step 3:** implement. **Step 4:** run → PASS.
- [ ] **Step 5:** commit `brain: sleep encodes the existing history through the same understanding`.

### Task 8: Whole-branch check on a copy, then hand to Laris

- [ ] **Step 1:** `python3 -m pytest tests/ -q` → all pass; `ruff check brain/ orchestrator/mind_hook.py tests/` → clean; `wc -l` every new/changed file ≤ 260 (except the pre-existing `orchestrator/orchestrator.py`).
- [ ] **Step 2:** on a copy of `brain.sqlite`, `chroma_data`, `chroma_chunks` (cwd = the copy, as in the 2026-11-21 probes), run `encode_slice` for 50 episodes and `expose` for 5 real messages of Laris's; read the 5 blocks yourself and paste them into the PR description, so Laris sees what GOAT will see. This is a reading check, not a metric.
- [ ] **Step 3:** open the PR with `[mind] enabled = false`; after merge, Laris decides when to switch it on (`config/brain.toml`, restart `goat2.service`) and judges in conversation (spec §5).
