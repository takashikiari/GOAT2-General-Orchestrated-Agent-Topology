# supervisor/classification/ — Intent Routing for GOAT 2.0

This package implements **pure LLM-based intent routing**. The LLM is the
only decision-maker; there are **zero hardcoded keywords, zero regex
short-circuits, zero short-circuit patterns** anywhere in the classification
path. The model receives the conversation context and user profile and
reasons semantically about what GOAT should do next.

## Directory

```
supervisor/classification/
├── __init__.py            — exports IntentDepth, classify_intent, DirectRequest, …
├── classifier.py          — pure LLM depth classifier (CONVERSATIONAL vs COMPLEX)
├── request_classifier.py  — direct-tool bypass (memory_recent / memory_get / file_read)
└── lang_detect.py         — LLM-based language identification
```

---

## Core Concept — DAG is GOAT's Internal Thought Process

GOAT 2.0 is a two-layer system:

- **GOAT (supervisor)** — the interface with the user. It carries the
  conversation, holds the user profile, monitors background work, and
  synthesizes results. It has full access to all three memory tiers.

- **DAG (deep thinking)** — a multi-agent task pipeline that GOAT spawns
  in the background when the user's request is too complex for direct
  conversational handling. The DAG writes its progress and results to
  **working memory**; GOAT reads from there and never blocks on the DAG.

**DAG is GOAT's internal thought process** — it runs asynchronously,
sends progress to a shared scratchpad, and finishes by writing a final
result. GOAT decides **when** to think deeply; the LLM that powers GOAT
makes this decision based on the user's intent and what GOAT is
currently capable of doing directly.

---

## IntentDepth — Three Routing Depths

`IntentDepth` is a three-value enum that captures the depth of work the
user's request requires:

| Depth | Meaning | Path |
|---|---|---|
| `CONVERSATIONAL` | GOAT can answer directly | single LLM call with memory + web_search |
| `ANALYTICAL` | Lightweight DAG (≤2 tasks) | small DAG, no critic re-run |
| `COMPLEX` | Full DAG | planner → researcher/coder/critic → summarizer |

The classifier used to be a two-value decision (CONVERSATIONAL vs
COMPLEX). It now uses three values so that lightweight requests can
benefit from a small DAG without paying the full critic+rerun overhead.

The enum is preserved exactly as it was — callers and validators that
import `IntentDepth` are unaffected.

---

## The Classifier — Pure LLM Reasoning

`classify_intent(intent, registry, is_first_message=False)` is the entry
point. The flow is:

1. The classifier gathers context:
   - What GOAT is **directly** capable of doing (16 memory tools + web
     search).
   - What requires the **DAG** (multi-step research, code generation,
     file operations on multiple files, deep analysis, architecture
     decisions, system checks).
   - The **conversation history** — what was just said.
   - The **user profile** from long-term memory (preferences, style,
     current projects).
   - **Active DAG sessions** — if GOAT is already thinking deeply, the
     classifier knows not to start another one.
   - **User override** — if the user explicitly asked for a different
     routing mode (e.g. "answer me directly" or "think deeply about
     this"), the override is applied before classification.
2. The classifier sends all of this context to the LLM.
3. The LLM replies with exactly one word: `conversational`,
   `analytical`, or `complex`.
4. The result is logged at DEBUG level with the full prompt, the
   response, and the chosen depth.
5. On parse failure, the classifier falls back to `CONVERSATIONAL`
   (safe default — never escalate to a full DAG on uncertainty).

### No Hardcoded Keywords

The classifier is **purely LLM-driven**. There are:

- ❌ no `re.compile(r"…")` patterns
- ❌ no `if "?" in text` checks
- ❌ no greeting lists
- ❌ no `help` detection
- ❌ no first-message length checks

If the user says "?", the LLM decides whether it's an inquiry or a
typo. If the user says "salut", the LLM decides whether it's a
greeting or a misspelled word in context. Every intent goes through
the same semantic path.

### What the LLM Sees

The system prompt lists GOAT's capabilities in plain language:

```
GOAT can answer directly when the request is a question, a definition,
a comparison it can do from memory, a small chitchat exchange, or a
trivial lookup using its own memory or web search.

GOAT should spawn the DAG (deep thinking) when the request requires
multi-step research, code generation across multiple files, deep
analysis of the codebase, system configuration, architecture
decisions, or any task that benefits from parallel sub-tasks.

Current conversation:
  <last 6 user turns>

Active DAG sessions:
  <none / one running with progress %>

User profile (long-term):
  <semantic summary of user preferences and projects>

User override:
  <none / "force CONVERSATIONAL" / "force COMPLEX">

Now classify the latest user message.
Reply with exactly one word: conversational, analytical, or complex.
```

The classifier never sees a regex, a list, or a score. It sees a
prompt.

---

## Working Memory as the Nervous System

Working memory (Redis) is the **nervous system** of GOAT 2.0:

- GOAT **writes** to working memory: turn summaries, GOAT↔DAG
  instructions, override flags.
- DAG **writes** to working memory: progress after each wave, final
  results, intermediate task outputs.
- GOAT **reads** from working memory: progress of any active DAG,
  results when a DAG finishes, override state.

The key namespaces are:

| Key pattern | Owner | Meaning |
|---|---|---|
| `goat:<session_id>:turn_<ts>` | GOAT | every turn (user + assistant summary) |
| `dag:<session_id>:progress`   | DAG  | current wave / total waves / status |
| `dag:<session_id>:result`     | DAG  | final result after all waves |
| `dag:<session_id>:task:<tid>` | DAG  | per-task intermediate output |
| `goat:<session_id>:override`  | GOAT | user override flag (force_conv / force_cplx) |

All keys are TTL-bound (default 3600s for DAG, 7200s for GOAT turns).
The nervous system is volatile by design.

---

## DAG Awareness in GOAT

`classify_intent` consults working memory before deciding. Specifically:

1. It calls `memory_manager.working.find_by_prefix("dag:")` to discover
   any active DAG sessions.
2. For each active session, it reads the `dag:<sid>:progress` key.
3. It builds a one-line summary: "DAG <sid> is at wave 3 of 5,
   status=running."
4. This summary is injected into the LLM prompt so the model knows the
   user might be asking about in-flight work.

If a DAG is **running** and the user asks a follow-up question, the
classifier can choose CONVERSATIONAL (so GOAT reports progress) or
COMPLEX (so the new request joins a new DAG), but it always does so
with full knowledge of what's in flight.

---

## DAG Progress Reporting

`WorkflowGraph` (in `supervisor/pipeline/workflow.py`) writes a
progress record to working memory after every wave completes:

```python
key   = f"dag:{session_id}:progress"
value = {
  "wave": 3,
  "total_waves": 5,
  "completed_tasks": ["t1", "t2", "t4"],
  "status": "running"   # or "complete"
}
ttl   = 3600
```

GOAT reads this on demand via the `query_dag_status` tool or the
`memory_get` tool with the progress key. The progress key is
overwritten in place after each wave — no append-only log, no
versioning.

When the final wave finishes, the `status` is set to `"complete"` and
the same key is updated one last time before the final result is
written to `dag:<session_id>:result`.

---

## Explicit User Control

Users can explicitly route their message without typing "DAG" as a
keyword:

- "Just answer me directly" / "nu mai rula DAG" / "tu singur" → force
  CONVERSATIONAL.
- "Think about this deeply" / "pornește DAG" / "gândește profund" →
  force COMPLEX.

Detection is **semantic**, not keyword-based. The override prompt
section is:

```
Does the user explicitly request a specific routing mode?
- "answer directly", "don't spawn DAG", "just reply" → force conversational
- "think deeply", "spawn DAG", "use the pipeline" → force complex
- Otherwise, no override.

Override: <none | "force conversational" | "force complex">
```

The LLM extracts the override semantically. The classifier then maps
the override to the corresponding `IntentDepth`.

The override is also stored in working memory for the rest of the
session: `goat:<session_id>:override` (TTL = session duration). On
subsequent turns, the classifier re-uses the stored override unless
the user changes it.

---

## Behavioral Learning via Episodic Memory

GOAT 2.0 learns **semantically** from user corrections. There are zero
hardcoded examples, zero ChromaDB seeding, zero "if the user said X,
do Y" rules.

The learning loop:

1. **Detection** — when the user disagrees with GOAT's routing, the
   disagreement is detected semantically by the LLM. Examples include
   "I didn't want a DAG, just a quick answer", "you should have just
   answered", "why is this taking so long?". The LLM looks for the
   semantic content of a correction, not specific keywords.

2. **Storage** — when a correction is detected, the supervisor writes
   a labeled example to **episodic memory** (ChromaDB):
   ```
   intent: "compare Python and Rust for our backend"
   goat_routed: complex
   user_wanted: conversational
   correction: "I just wanted a quick comparison, not a full
                 research project"
   ```

3. **Retrieval** — on the next similar intent, the supervisor queries
   episodic memory for past corrections whose `intent` is semantically
   close. The LLM sees them as context:
   ```
   The user has previously corrected similar intents:
   - "I just wanted X, not a full pipeline" (preferred: conversational)
   - "Just give me the answer" (preferred: conversational)
   - "I need a deep analysis on this" (preferred: complex)
   Use these as soft hints. They do not override the override flag.
   ```

4. **Adaptation** — the LLM uses the corrections as soft signals.
   Adaptability comes from semantic understanding, not pattern
   matching. There is no "if correction_count > 3, force
   conversational" rule. The LLM weighs the correction alongside
   everything else in the prompt.

### What is NOT Behavioral Learning

- ❌ ChromaDB collections seeded with hardcoded examples.
- ❌ A "user profile" attribute list (e.g. `prefers_short_answers: True`).
- ❌ Regex-based correction detection.
- ❌ Counting corrections and switching on the count.

The user profile in long-term memory is a **semantic summary** of the
user, written by an LLM and updated as new signals arrive. It is
consulted by the classifier as plain prose.

---

## Strict Memory Separation

| Tier | Owner | Contents |
|---|---|---|
| **Working (Redis)** | GOAT ↔ DAG | turn summaries, override flag, DAG progress, DAG results, intermediate task outputs |
| **Episodic (ChromaDB)** | GOAT only | past turns, learned corrections, conversation patterns |
| **Long-term (Letta)** | GOAT only | user profile (semantic summary), preferences, behavior style |

Rules:

- **DAG has zero access** to episodic or long-term. DAG agents only
  see working memory (Redis), and only their own `dag:*` namespace.
- **Episodic and long-term are supervisor-only**. No DAG agent ever
  reads or writes there.
- **DAG execution data never pollutes** episodic or long-term. Progress
  reports, intermediate outputs, and final results stay in working
  memory and expire with their TTL.

The classifier is allowed to read from all three tiers (it is a
supervisor component) but it never writes to episodic or long-term
from the classification path — behavioral examples are written by
the supervisor's learning loop, not by the classifier.

---

## Files in Detail

### classifier.py
- `IntentDepth` (Enum) — three routing depths.
- `classify_intent(intent, registry, is_first_message=False)` — async,
  LLM-driven, no hardcoded patterns.
- Builds a single LLM prompt with: GOAT capabilities, DAG awareness,
  conversation history, user profile, override, behavioral hints.
- Logs the prompt, response, and chosen depth at DEBUG level.
- Returns `IntentDepth.CONVERSATIONAL` on parse failure.

### request_classifier.py
- Lightweight rule-based classifier for direct tool bypass.
- Bypasses DAG for: `memory_recent`, `memory_get`, `file_read`.
- Conservative — rejects multi-step indicators.
- Pattern-based but **separate from the depth classifier**; the depth
  classifier never uses these patterns.

### lang_detect.py
- LLM-driven language detection for the user message.
- Returns the dominant language name in English.
- Falls back to `"English"` on any failure.

---

## Verification

```python
from supervisor.classification.classifier import classify_intent
print("ok")  # import succeeds
```

This is the canonical import test — it must succeed without
ImportError, without the supervisor's DAG ever needing to be
running, and without pulling in any circular imports.
