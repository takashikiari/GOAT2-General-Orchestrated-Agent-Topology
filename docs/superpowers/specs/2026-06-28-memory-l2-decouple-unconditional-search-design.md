# Memory: Decouple L2 from AITS, unconditional L3 search, post-search similarity filtering

Date: 2026-06-28
Status: Design (awaiting review)
Replaces: the classifier-fix + recency-guard plan (discarded — see "Discarded plan" below).

## Problem (root cause, confirmed by code + logs)

L3 starves on long-conversation / complex turns. The observed logs:

- Turn 1 (`07:54:42`): budget 6000, `tokens_l2=4090`, `tokens_l3=1731`, `prefetch_blocks_used=0` (a dead metric — see §5).
- Turn 2 (`07:57:48`): budget 4352, `tokens_l2=4191`, `tokens_l3=0`, `prefetch_attempted=false`.

**Root cause:** L2 sizing is derived from the AITS budget — it has no independent cap — so L2 consumes most of the budget remainder and L3 only ever receives a fixed 30% fraction (`L3_RESERVE_FRACTION`), and **0 when no prefetch results were returned** (`has_l3=False`). L2 and L3 compete for the same `remaining` pool. The fix is structural (decouple L2, send AITS to L3), not a better intent classifier.

## Discarded plan (and why)

Earlier proposal: fix the AITS confidence classifier (drop single-token `_LOW_CONFIDENCE_WORDS` membership; "la" false-positive), add a token-overlap "not-in-L2" recency guard, optionally a topic-keyed L2.5 cache.

**Discarded — structurally fragile.** Both the classifier and the recency guard decide on message **form**, not semantic content. Concrete counterexample: *"Goat mai ții minte când ți-am spus salut azi?"* is a genuine recall question containing the greeting word "salut" — any word-list / keyword classifier (including the compound greeting-phrase variant) risks misclassifying it, and a 4-content-token query makes token-overlap oversensitive to a single coincidental match. Patching "la" only moves the false positive to the next common preposition ("și", "pe", "cu", "de"). The form-based approach is abandoned entirely.

**Also rejected en route:** a fixed token/message cap on L2 (`L2_MAX_MESSAGES`/`L2_MAX_TOKENS`). It is the *same static-threshold mistake moved to a different variable* — a hardcoded number with no principled value, calibrated on today's message sample, that goes stale as conversation patterns shift. Replaced by the dynamic priority-inverted split (§1–2): the only static number left is the L3 *floor*, and it is anchored to observed L3 injection sizes, not to message length.

## Verified citations

Every claim is grounded in the code (line numbers current as of 2026-06-28):

- **AITS budget calc:** `orchestrator/orchestrator.py:140` — `budget = calculate_intent_budget(confidence, complexity)` (classify stage, runs before search).
  - Formula: `memory/aits.py:112-117` — `base + confidence·multiplier + complexity·bonus`, capped `BUDGET_HARD_CAP=12000`.
- **L2 sizing (the coupling):** `memory/layers.py:221` — `l2_cap, _l3_reserve = allocate_context_budget(mandatory_tokens, budget, has_l3=bool(l3_results))`; then `:226` `trimmed = self._trim_recent_messages(messages, l2_cap)`.
  - `memory/context_budget.py:36-42`:
    ```python
    remaining = max(budget - mandatory_tokens, 0)
    l3_reserve = int(remaining * L3_RESERVE_FRACTION) if (has_l3 and remaining) else 0
    l2_cap = min(L2_CONTEXT_CAP, max(remaining - l3_reserve, 0))
    if l2_cap < L2_FLOOR_TOKENS:
        l2_cap = min(L2_FLOOR_TOKENS, remaining)
        l3_reserve = max(remaining - l2_cap, 0)
    ```
  - Constants: `memory/config.py` — `L2_CONTEXT_CAP=8000`, `L3_RESERVE_FRACTION=0.3`, `L2_FLOOR_TOKENS=500`, `MAX_CONTEXT_TOKENS=4000`.
  - ⇒ L2 cap = `min(8000, budget - mandatory - l3_reserve)`. **No independent cap.** L3 gets 30% of `remaining` only when `has_l3=True`, else 0.
- **Pre-search gating (to be removed):** `orchestrator/orchestrator.py:157` — `if confidence >= PREFETCH_CONFIDENCE_THRESHOLD:` (0.4, `memory/config.py`). `categorize_intent` at `memory/observability_collector.py:94-104` is the coarse confidence-tier derivation (no real classifier).
- **L3 fit (no similarity filter today):** `memory/layers.py:233-239` — `l3_budget = budget - mandatory_tokens - l2_tokens`; `_fit_search_results` (`:281-300`) greedily fits results by **token budget only**, closest-first, no score threshold.
- **Episodic search discards distances:** `memory/episodic/episodic.py:74-79` —
  ```python
  kw = {"query_texts": [query], "n_results": limit}
  ...
  results = await asyncio.to_thread(self._get_collection().query, **kw)
  docs, metas = results["documents"][0], results["metadatas"][0]
  return [{"content": d, "metadata": m} for d, m in zip(docs, metas)]
  ```
  `results["distances"]` (ChromaDB's similarity score) is available but **not returned**.
- **Distance metric is L2, not cosine (confirmed):** `memory/episodic/episodic.py:40` `get_or_create_collection(EPISODIC_COLLECTION_NAME)` passes **no `metadata=` / no `hnsw:space`**; the live collection's `metadata` is `None` → ChromaDB's default `hnsw:space = "l2"` (squared Euclidean). Empirical check on the live 13-doc collection: distances cluster at 1.2–1.6 (e.g. `"prefetch tokens" → [1.34, 1.53, 1.55, 1.57, 1.58]`). Magnitudes in the 0–4 band (not tens) indicate the default ONNX MiniLM embeddings are effectively unit-normalized, so squared-L2 = `2(1 − cos)` (range 0–4): distance `1.0 ≈ cos 0.5`, `1.2 ≈ cos 0.4`, `2.0 = orthogonal`. ⇒ `L3_SIMILARITY_MAX_DISTANCE` is an L2 squared-distance floor, **not** a cosine-space threshold; `1.0` means "keep results with cosine similarity ≥ ~0.5."
- **Embedding model is local:** `memory/episodic/episodic.py:36-40` — `chromadb.PersistentClient(...)` + `get_or_create_collection(EPISODIC_COLLECTION_NAME)` with **no `embedding_function`** → ChromaDB default (`all-MiniLM-L6-v2` via local ONNX runtime; model downloaded once, then in-process). No external API, no per-call network cost.
- **Search cache key (point 6):** `memory/layers.py:155` `cache_key = self._search_cache_key(query)`; `:164-168`:
  ```python
  @staticmethod
  def _search_cache_key(query: str) -> str:
      digest = hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]
      return f"{_SEARCH_NAMESPACE}:{digest}"
  ```
  `query` = `intent` (the full user message) passed at `orchestrator/orchestrator.py:162`. ⇒ key = `search:{sha256(full_message)[:16]}`. Each turn's message is unique → the cache essentially never hits; it only adds a Redis GET (miss) + SET per search.
- **Dead metrics:**
  - `prefetch_blocks_used` hardcoded `0`: `orchestrator/orchestrator.py:176` — `collector.set_prefetch(..., len(l3_results), 0)`. Real usage is `l3_used`, returned by `assemble_context` at `:179` but never fed back.
  - `cache_key` never populated: `memory/layers.py:144-162` `search_episodic_with_cache` returns `(results, cache_hit)` — the key is computed (`:155`) but not returned; `memory/observability_collector.py:78-81` `set_cache` comment says "The key is internal, not stored." `memory/observability.py:48` `cache_key: Optional[str] = None` stays null.
- **Inject stage mislabels LLM time:** `orchestrator/orchestrator.py:183-227` — the "inject" stage wraps prompt build, the LLM call (`:203` `chat.completions.create`), the optional tool-round LLM call (`:213`), the DSML round (`:214-219`), and L2 persist (`:223-226`). The ~30s is the DeepSeek API (`config/settings.py` `TIMEOUT_SECONDS=30.0`, `MAX_TOKENS=2048`, model `deepseek-v4-flash`), not memory injection. Latency fields: `memory/observability.py:50-55`.

## Design

### 1–2. Dynamic, priority-inverted L2/L3 split

**Invert the priority:** L3 gets first claim on a guaranteed minimum slice; L2 gets whatever's left, which still scales naturally with AITS (confident/complex turns → bigger budget → more L2 room; simple turns → less). Nothing is static except the L3 *floor*, and that floor is anchored to observed L3 injection sizes, not to message length — so it can't "go stale" the way a fixed L2 cap or a keyword list would.

```
l3_guarantee = L3_MIN_GUARANTEE_TOKENS                 # always reserved, nonzero by construction
l2_cap       = budget − tokens_l0_l1 − l3_guarantee    # L2 gets the rest (AITS-scaled)
tokens_l3    = up to (budget − tokens_l0_l1 − tokens_l2_actual)   # ≥ l3_guarantee, always
               then similarity-filtered (§4)
```

- L2 underfills its cap on a simple turn → L3 grows into the slack (up to `budget − l0/l1 − l2_actual`, always `≥ l3_guarantee`).
- L3 underfills its guarantee on an off-topic turn (few results pass the §4 similarity filter) → the reserved-but-unused tokens become slack. That slack is the **accepted price of never starving L3 by construction** — the direct fix for the observed `tokens_l3=0` starvation.

**Where the swap goes (citation):** `memory/context_budget.py:36-42` — the current body

```python
remaining  = max(budget - mandatory_tokens, 0)
l3_reserve = int(remaining * L3_RESERVE_FRACTION) if (has_l3 and remaining) else 0
l2_cap     = min(L2_CONTEXT_CAP, max(remaining - l3_reserve, 0))
if l2_cap < L2_FLOOR_TOKENS:
    l2_cap     = min(L2_FLOOR_TOKENS, remaining)
    l3_reserve = max(remaining - l2_cap, 0)
return l2_cap, l3_reserve
```

is replaced by:

```python
def allocate_context_budget(mandatory_tokens: int, budget: int) -> tuple[int, int]:
    """Priority-inverted: L3 gets a guaranteed minimum first; L2 takes the rest (AITS-scaled)."""
    available    = max(budget - mandatory_tokens, 0)
    l3_guarantee = L3_MIN_GUARANTEE_TOKENS
    l2_cap       = available - l3_guarantee
    # Pathological-budget guard: if reserving the guarantee would breach L2's floor,
    # the floor wins and the guarantee is reduced to the remainder (>=0). Never binds
    # on realistic budgets (see "Starting value" below for the arithmetic).
    if l2_cap < L2_FLOOR_TOKENS:
        l2_cap       = min(L2_FLOOR_TOKENS, available)
        l3_guarantee = max(available - l2_cap, 0)
    return l2_cap, l3_guarantee
```

- The `has_l3` parameter is **removed** — the guarantee is reserved regardless of whether results exist (consistent with §3's unconditional search); the caller at `memory/layers.py:221` is updated.
- `assemble_context` (`memory/layers.py:190-240`) computes `l3_budget = max(budget − mandatory_tokens − l2_tokens_actual, 0)` (this is `≥ l3_guarantee` by construction, since `l2_tokens ≤ l2_cap = available − l3_guarantee`), then similarity-filters (§4) and fits.
- Constants retired from the split: `L3_RESERVE_FRACTION`, `L2_CONTEXT_CAP`. `L2_FLOOR_TOKENS` stays only as the pathological-budget guard. New constant: `L3_MIN_GUARANTEE_TOKENS` in `memory/config.py`.

**Starting value for `l3_guarantee` — anchored to observed L3 sizes, not a guess:**

- Observed successful L3 injections across sessions: **1159–1731 tokens** (today's turn 1: `tokens_l3=1731` for 13 results ≈ 133 tok/result; the 1159 low end is from prior-session log arithmetic).
- The guarantee must cover a **realistic single-result-set injection**: `1200` sits at/above the low end (1159), so a minimal real result set always fits within the guaranteed floor; larger sets (up to 1731) claim the extra from unused L2 slack.
- Upper bound from the minimum-budget / L2-floor constraint: guarantee ≤ `min_budget − mandatory − L2_FLOOR`. Theoretical base budget 2000: `2000 − 102 − 500 = 1398`; realistic minimum (~2800, `BUDGET_BASE + 0.2·BUDGET_CONFIDENCE_MULTIPLIER`): ~2198. `1200` sits comfortably under both → reserving it never breaches L2's floor.
- ⇒ `L3_MIN_GUARANTEE_TOKENS = 1200` (tune empirically via §Verification step 2). It is the top of the 800–1200 candidate range and just above the smallest observed successful injection.

### 3. Drop all pre-search gating — fire episodic search every turn

Remove the `if confidence >= PREFETCH_CONFIDENCE_THRESHOLD` gate (`orchestrator/orchestrator.py:157-174`); call `search_episodic_with_cache` (or `search_episodic`) unconditionally each turn.

- **Latency is negligible:** observed `latency_search = 0.20s` (turn 1, 13 results) vs `latency_inject ≈ 9-30s` (the LLM call). The embedding model is local ONNX (point 7), so per-call cost is local CPU embedding + local HNSW vector query — no network, no API, no variance from external services.
- **Verification step (see §Verification):** benchmark `search` at the current collection size and at 5×/10× to confirm `latency_search` stays < ~0.5s. Expected to hold (local, HNSW sub-linear), but confirmed empirically before declaring it negligible at scale.
- `prefetch_*` fields in observability are reinterpreted: `prefetch_attempted`/`prefetch_succeeded` now mean "search ran / returned results" (always attempted), `prefetch_timeout` stays for the `asyncio.wait_for` timeout path (`PREFETCH_TIMEOUT=0.5`, `orchestrator/orchestrator.py:161-168`). `confidence`/`complexity`/`intent_category` remain computed for observability but no longer gate retrieval. `categorize_intent` can stay as a coarse label or be dropped — no functional dependency on it.

### 4. Filter L3 injection post-search using ChromaDB similarity score (not query form)

Replace form-based gating with **content-semantic** filtering:

- `memory/episodic/episodic.py:62-79` `search` is changed to capture and return `distances`: each result becomes `{"content", "metadata", "score": distance}` (lower = closer under ChromaDB default L2).
- A relevance floor constant `L3_SIMILARITY_MAX_DISTANCE` (config) drops results whose squared-L2 distance exceeds the threshold **before** token-budget fitting. This is a new step in `_fit_search_results` (`memory/layers.py:281-300`) or a dedicated `_filter_by_score` before it.
- Net: every turn searches, but only semantically-close results are injected. Recall questions get relevant L3; off-topic turns search and inject nothing (results filtered out) — no token waste, decided by ChromaDB's own semantic distance, not by query wording. This is what the classifier/recency-guard tried to approximate and couldn't.

**Starting threshold value `L3_SIMILARITY_MAX_DISTANCE = 1.0`** — an L2 squared-distance floor ≈ cosine similarity ≥ 0.5 (confirmed metric above; the embeddings' unit-norm property is inferred from observed distance magnitudes, not from the code). It is a deliberately tight floor: genuinely relevant matches should land well under 1.0 (cos > 0.5), while loosely-related hits are filtered. The current 13-doc collection has nothing below 1.2 because it is small and homogeneous — correct to filter. The threshold is calibrated empirically (see §Verification V3) against a labeled set of (query, result, relevant/irrelevant) pairs; the present collection is too small/homogeneous to calibrate against, so V3 waits for a richer labeled set.

### 5. Wire the dead metrics; split `latency_inject`

- **`prefetch_blocks_used` → real `l3_used`:** move the `blocks_used` assignment to **after** `assemble_context` (`orchestrator/orchestrator.py:179`, which already returns `l3_used`). Either move the `set_prefetch` call past line 179, or add `collector.set_prefetch_used(l3_used)` invoked after assemble. The literal `0` at `:176` is removed.
- **`cache_key` → reported:** change `search_episodic_with_cache` (`memory/layers.py:144-162`) to return `(results, cache_hit, cache_key)`; have the orchestrator pass the key to a new `collector.set_cache_key(key)` (or extend `set_cache`, `memory/observability_collector.py:78`). `MemoryObservation.cache_key` (`memory/observability.py:48`) is then populated. (This is reporting the already-generated key — see §Deferred for the cache *behavior* decision.)
- **Split `latency_inject`:** add two latency stages around the work currently lumped into "inject" (`orchestrator/orchestrator.py:183-227`):
  - `latency_inject` — prompt block assembly + guidance concatenation (small).
  - `latency_llm` — the `chat.completions.create` call(s), including the tool round and DSML round (the ~30s lives here).
  - `latency_save` — L2 persist (`get_working_context` + `save_working_context`, `:223-226`).
  - Add `latency_llm` and `latency_save` fields to `MemoryObservation` (`memory/observability.py:50-55`); keep `latency_inject` redefined to prompt-assembly only. `set_context_from_blocks` / `finish` and `memory/analytics.py` aggregates updated accordingly. The 30s is then honestly attributed to the LLM, not "injection."

## Deferred: search cache behavior

Per the directive, the cache **keying/behavior** decision is deferred. The verified facts that inform it:

- Key is `search:{sha256(full_message)[:16]}` (`memory/layers.py:155,164-168`), keyed on the full message → essentially never hits; adds a Redis GET (miss) + SET per search.
- With unconditional search every turn (§3), the cache is even less useful — and since the embedding is local (point 7), there is no expensive call for the cache to save.

This spec makes **no change to cache keying**. It only wires the existing key into observability (§5). The keep-vs-remove-vs-rekey decision is a separate, later step after §3/§4 are in and metrics show whether any repeat-search pattern exists worth caching.

## Verification

1. **Search latency at volume:** benchmark `EpisodicMemory.search` at current count and at 5×/10× (synthesize entries) — confirm `latency_search` < ~0.5s, supporting §3's "negligible vs LLM" claim. Fails → revisit unconditional search.
2. **L3 never starves, and the floor is right:** after §1–2, across a recall turn, a simple turn, and the min-budget turn, confirm: (a) `tokens_l3 ≥ L3_MIN_GUARANTEE_TOKENS` whenever L3 results pass the similarity filter — **never 0**; (b) `tokens_l3 = budget − L0/L1 − tokens_l2_actual` when L3 fills (no longer capped at 30% of remaining); (c) `tokens_l2 ≤ budget − L0/L1 − L3_MIN_GUARANTEE_TOKENS`; (d) on the min-budget (~2800) turn, `tokens_l2 ≥ L2_FLOOR_TOKENS` (the guarantee never breaches L2's floor). Then sweep `L3_MIN_GUARANTEE_TOKENS` over 800–1400 and pick the value that maximizes relevant-L3-injected across the recall set from step 3 without dropping L2 below its floor on the min-budget turn.
3. **Similarity threshold tuning:** build a small labeled set from real (query → stored memory) pairs (relevant/irrelevant), sweep `L3_SIMILARITY_MAX_DISTANCE`, pick the value maximizing relevant-kept / irrelevant-dropped. Confirm the "salut azi" recall question returns its target memory above threshold while off-topic turns return nothing.
4. **Metric honesty:** confirm `prefetch_blocks_used == results_used == l3_used` on a turn that injects L3, and that `cache_key` is non-null on a search turn. Confirm `latency_llm` carries the ~30s and `latency_inject` is small.
5. **No regression:** existing tests in `tests/test_context_budget.py`, `tests/test_episodic_queries.py` updated for the new L2-fixed / L3-AITS split and the `search` signature change (returns `score`).

## Out of scope

- A real intent classifier / embedding-based gating — explicitly not pursued (form-based gating discarded; §4 supersedes).
- Stateful orchestrator (to stop L0/L1 re-injection) — not pursued; L0/L1 (~102 tokens) stays.
- Search cache keying/behavior — deferred (above).
- L1 facts remain empty (no `store_fact` writer) — separate data gap, unchanged.