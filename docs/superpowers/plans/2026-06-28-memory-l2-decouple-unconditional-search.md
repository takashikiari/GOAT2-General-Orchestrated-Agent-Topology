# Memory: Decouple L2 from AITS, unconditional L3 search, post-search similarity filtering — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop L3 from starving on long/complex turns by inverting the L2/L3 budget priority (L3 guaranteed minimum first, L2 takes the rest, AITS-scaled), make episodic search unconditional, filter L3 injection by ChromaDB similarity score (not query form), and fix the dead/mislabeled observability metrics.

**Architecture:** `allocate_context_budget` becomes priority-inverted (L3 floor `L3_MIN_GUARANTEE_TOKENS` reserved before L2's cap is computed). `assemble_context` computes `l3_budget` from actual L2 usage (≥ guarantee by construction) and similarity-filters L3 results before token-fitting. `EpisodicMemory.search` surfaces ChromaDB `distances` as `score`. The orchestrator drops the confidence gate (always searches), wires `prefetch_blocks_used` to the real `l3_used`, reports `cache_key`, and splits the mislabeled `latency_inject` into `inject` (prompt build) / `llm` (the ~30s API call) / `save` (L2 persist). The search cache *keying/behavior* is explicitly deferred (no change); only the already-generated key gets reported.

**Tech Stack:** Python 3.12, asyncio, ChromaDB (local ONNX MiniLM-L6-v2 embeddings, `PersistentClient`), Redis (L2 working + L2.5 session cache), DeepSeek OpenAI-compatible API, pytest (`asyncio.run` convention), TOML config (`config/memory.toml`).

## Global Constraints

- File-size rule: max 90 lines/file, single responsibility, split before writing (project convention — see `~/.claude/.../feedback_file_size_rule.md`).
- No regex for classification/tokenization (project rule 5; existing code uses `str.split`/`startswith`).
- Embedding model is **local** (`memory/episodic/episodic.py:36-40`, no `embedding_function` → ChromaDB default ONNX MiniLM) — unconditional search has no per-call network cost.
- AITS budget range 2000–12000 (`memory/aits.py:112-117`, `BUDGET_BASE=2000`, `BUDGET_HARD_CAP=12000`).
- Test convention: `asyncio.run(...)` for async calls; fake backends (see `tests/test_episodic_queries.py` `_FakeCollection`, `tests/test_orchestrator_plugins.py` `_Reg`).
- Commit only when the user asks (harness rule); each task's commit step is written but should be run only on user approval, or skipped if the user prefers to commit as one batch at the end.

**Spec:** `docs/superpowers/specs/2026-06-28-memory-l2-decouple-unconditional-search-design.md`

---

## File Structure

- `config/memory.toml` — add two `[retrieval_budget]` keys (`l3_min_guarantee_tokens`, `l3_similarity_max_distance`).
- `memory/config.py` — parse + export the two new constants.
- `memory/context_budget.py` — rewrite `allocate_context_budget` (priority-inverted, drop `has_l3`).
- `memory/episodic/episodic.py` — `search` returns `score` (ChromaDB `distances`).
- `memory/layers.py` — `assemble_context` (new split + similarity filter); `search_episodic_with_cache` returns 3-tuple incl. `cache_key`.
- `memory/observability.py` — add `latency_llm`, `latency_save` fields.
- `memory/observability_collector.py` — `set_cache` accepts `key`; new `set_prefetch_blocks_used`.
- `memory/analytics.py` — aggregate `latency_llm`, `latency_save`.
- `orchestrator/orchestrator.py` — drop confidence gate (unconditional search), unpack 3-tuple, report `cache_key`, wire `prefetch_blocks_used`, split inject/llm/save stages.
- `tests/test_context_budget.py` — rewrite allocate tests; add assemble split/filter tests.
- `tests/test_episodic_queries.py` — add `search` returns-score test.
- `tests/_orch_fakes.py` — NEW shared fakes for orchestrator tests.
- `tests/test_orchestrator_memory_flow.py` — NEW orchestrator behavior tests.

---

## Task 1: Config constants for L3 guarantee and similarity threshold

**Files:**
- Modify: `config/memory.toml` (`[retrieval_budget]` section)
- Modify: `memory/config.py` (`_DEFAULTS["retrieval_budget"]`, parsing block, `__all__`)
- Test: `tests/test_config_constants.py` (NEW)

**Interfaces:**
- Produces: `memory.config.L3_MIN_GUARANTEE_TOKENS: Final[int]` (=1200), `memory.config.L3_SIMILARITY_MAX_DISTANCE: Final[float]` (=1.0). Consumed by Tasks 2 and 4.

**Metric confirmation (cited + empirical):** `episodic.py:40` `get_or_create_collection(EPISODIC_COLLECTION_NAME)` passes no `metadata=`/`hnsw:space`; the live collection's `metadata` is `None` → ChromaDB default `hnsw:space = "l2"` (squared Euclidean), **not cosine**. Live 13-doc distance check: 1.2–1.6 band ⇒ embeddings effectively unit-norm ⇒ squared-L2 = `2(1−cos)`, so `1.0 ≈ cos 0.5`. `L3_SIMILARITY_MAX_DISTANCE` is therefore an L2 squared-distance floor (≈ keep cosine similarity ≥ 0.5), not a cosine-space threshold. See spec §4.

- [ ] **Step 1: Write the failing test**

Create `tests/test_config_constants.py`:
```python
"""tests.test_config_constants — new retrieval_budget constants load with defaults."""
from memory.config import L3_MIN_GUARANTEE_TOKENS, L3_SIMILARITY_MAX_DISTANCE


def test_l3_guarantee_default():
    assert L3_MIN_GUARANTEE_TOKENS == 1200


def test_similarity_threshold_default():
    assert isinstance(L3_SIMILARITY_MAX_DISTANCE, float)
    assert L3_SIMILARITY_MAX_DISTANCE == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config_constants.py -v`
Expected: FAIL with `ImportError: cannot import name 'L3_MIN_GUARANTEE_TOKENS'`

- [ ] **Step 3: Write minimal implementation**

In `config/memory.toml`, add the two keys to the `[retrieval_budget]` section (after `l2_floor_tokens = 500`):
```toml
l3_min_guarantee_tokens = 1200
# L2 squared-Euclidean distance floor (ChromaDB default hnsw:space = "l2", no override at
# episodic.py:40). On unit-norm MiniLM embeddings, squared-L2 = 2(1-cos), so 1.0 ≈ cos 0.5.
# Calibrate empirically (Verification V3) once a richer labeled collection exists.
l3_similarity_max_distance = 1.0
```

In `memory/config.py`, add to `_DEFAULTS["retrieval_budget"]` (after `"l2_floor_tokens": 500,`):
```python
        "l3_min_guarantee_tokens": 1200,
        "l3_similarity_max_distance": 1.0,
```

In `memory/config.py`, after the `L2_FLOOR_TOKENS` parsing block (after line 117), add:
```python
L3_MIN_GUARANTEE_TOKENS: Final[int] = int(
    _retrieval_budget.get("l3_min_guarantee_tokens", _DEFAULTS["retrieval_budget"]["l3_min_guarantee_tokens"])
)
L3_SIMILARITY_MAX_DISTANCE: Final[float] = float(
    _retrieval_budget.get("l3_similarity_max_distance", _DEFAULTS["retrieval_budget"]["l3_similarity_max_distance"])
)
```

In `memory/config.py` `__all__`, add (after `"L2_FLOOR_TOKENS",`):
```python
    "L3_MIN_GUARANTEE_TOKENS",
    "L3_SIMILARITY_MAX_DISTANCE",
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config_constants.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add config/memory.toml memory/config.py tests/test_config_constants.py
git commit -m "feat(memory): add L3_MIN_GUARANTEE_TOKENS + L3_SIMILARITY_MAX_DISTANCE config"
```

---

## Task 2: Priority-inverted `allocate_context_budget`

**Files:**
- Modify: `memory/context_budget.py` (full rewrite of module body)
- Modify: `tests/test_context_budget.py` (replace the five `allocate_context_budget` tests at lines 14-49)

**Interfaces:**
- Consumes: `L3_MIN_GUARANTEE_TOKENS`, `L2_FLOOR_TOKENS` from Task 1.
- Produces: `allocate_context_budget(mandatory_tokens: int, budget: int) -> tuple[int, int]` returning `(l2_cap, l3_guarantee)`. **Signature changed: `has_l3` removed.** Consumed by Task 4.

- [ ] **Step 1: Write the failing tests**

In `tests/test_context_budget.py`, replace lines 14-49 (the five tests `test_l2_capped_to_budget_share_when_l3_present` through `test_zero_budget_yields_zero`) with:
```python
# --- allocate_context_budget: priority-inverted (L3 guaranteed first) -----------

def test_l3_guarantee_reserved_and_l2_takes_remainder():
    """Realistic budget: L3 gets its 1200 guarantee; L2 gets the rest (AITS-scaled)."""
    l2_cap, l3_guarantee = allocate_context_budget(mandatory_tokens=10, budget=4035)
    # available = 4025; l3_guarantee = 1200; l2_cap = 4025 - 1200 = 2825.
    assert l3_guarantee == 1200
    assert l2_cap == 2825
    assert l2_cap < 4035                       # L2 does not eat the whole budget


def test_l3_guarantee_never_zero_on_realistic_budget():
    """Even with no L3 results (search unconditional), the guarantee is reserved."""
    l2_cap, l3_guarantee = allocate_context_budget(mandatory_tokens=10, budget=4035)
    assert l3_guarantee > 0


def test_max_budget_l2_scales_with_aits_no_context_cap():
    """No L2_CONTEXT_CAP anymore: L2 grows with budget; L3 still only the guarantee floor."""
    l2_cap, l3_guarantee = allocate_context_budget(mandatory_tokens=10, budget=12000)
    assert l3_guarantee == 1200
    assert l2_cap == 12000 - 10 - 1200          # 10790 — L2 scales, no 8000 cap


def test_min_realistic_budget_keeps_l2_floor():
    """Min realistic AITS (~2800): guarantee reserved, L2 stays above its floor."""
    l2_cap, l3_guarantee = allocate_context_budget(mandatory_tokens=10, budget=2800)
    assert l3_guarantee == 1200
    assert l2_cap == 1590                       # 2790 - 1200
    assert l2_cap >= 500                         # L2 floor respected


def test_pathological_tiny_budget_l2_floor_wins():
    """Sub-floor budget: L2 floor wins, guarantee shrinks to the remainder (>=0)."""
    l2_cap, l3_guarantee = allocate_context_budget(mandatory_tokens=10, budget=510)
    # available = 500; can't honour 500 floor + 1200 guarantee -> floor wins, guarantee -> 0.
    assert l2_cap == 500
    assert l3_guarantee == 0


def test_zero_budget_yields_zero():
    l2_cap, l3_guarantee = allocate_context_budget(mandatory_tokens=10, budget=0)
    assert l2_cap == 0 and l3_guarantee == 0
```
Keep the rest of the file (`_msg`, the `_trim_recent_messages` tests) unchanged. The import line `from memory.context_budget import allocate_context_budget` stays.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_context_budget.py -v`
Expected: FAIL — the old 3-arg signature raises `TypeError: allocate_context_budget() got an unexpected keyword argument 'has_l3'` (or `assert l3_guarantee == 1200` fails against the old `l3_reserve`).

- [ ] **Step 3: Write minimal implementation**

Replace the entire body of `memory/context_budget.py` with:
```python
"""memory.context_budget — priority-inverted AITS split across L2 and L3.

L3 gets first claim on a guaranteed minimum token slice (``L3_MIN_GUARANTEE_TOKENS``)
so it is never starved to zero by construction on realistic budgets; L2 takes the
remainder and so stays AITS-scaled (bigger budget -> more L2 room). This replaces
the old ``min(L2_CONTEXT_CAP, remaining - l3_reserve)`` split where L2 ate the
budget and L3 only ever got a 30% fraction (and 0 when ``has_l3`` was false). The
``L2_FLOOR_TOKENS`` guard keeps L2 alive on pathological sub-floor budgets; the
guarantee then shrinks to the remainder (``>= 0``). That guard never binds on
realistic AITS budgets (>= ~1700 after mandatory).
"""
from __future__ import annotations

from memory.config import L2_FLOOR_TOKENS, L3_MIN_GUARANTEE_TOKENS

__all__ = ["allocate_context_budget"]


def allocate_context_budget(
    mandatory_tokens: int, budget: int,
) -> tuple[int, int]:
    """Return ``(l2_cap, l3_guarantee)`` for the budget remaining after L0+L1.

    L3 has first claim on ``L3_MIN_GUARANTEE_TOKENS``; L2's cap is the remainder
    (``budget - mandatory - l3_guarantee``), so L2 scales with AITS while L3 is
    guaranteed. ``l3_guarantee`` is reserved regardless of whether L3 results
    exist (search is unconditional). On a budget too small to honour both L2's
    floor and the L3 guarantee, the L2 floor wins and the guarantee shrinks to
    the remainder (``>= 0``); this guard never binds on realistic budgets.

    Args:
        mandatory_tokens: estimated tokens already spent on mandatory L0+L1.
        budget: AITS per-intent token budget.

    Returns:
        l2_cap: max tokens L2 may occupy (``>= 0``; ``<= available - guarantee``
            on realistic budgets, reduced to ``L2_FLOOR_TOKENS`` only sub-floor).
        l3_guarantee: tokens reserved for L3 (``L3_MIN_GUARANTEE_TOKENS`` on
            realistic budgets; the remainder on sub-floor budgets).
    """
    available = max(budget - mandatory_tokens, 0)
    l3_guarantee = L3_MIN_GUARANTEE_TOKENS
    l2_cap = max(available - l3_guarantee, 0)
    if l2_cap < L2_FLOOR_TOKENS:                       # L2 floor wins on tiny budgets
        l2_cap = min(L2_FLOOR_TOKENS, available)
        l3_guarantee = max(available - l2_cap, 0)
    return l2_cap, l3_guarantee
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_context_budget.py -v`
Expected: PASS (all allocate tests + the `_trim_recent_messages` tests)

- [ ] **Step 5: Commit**

```bash
git add memory/context_budget.py tests/test_context_budget.py
git commit -m "feat(memory): priority-inverted allocate_context_budget (L3 guaranteed first)"
```

---

## Task 3: Episodic search returns ChromaDB similarity score

**Files:**
- Modify: `memory/episodic/episodic.py:62-79` (`search` method)
- Modify: `tests/test_episodic_queries.py` (add a `_FakeCollection.query` + a search test)

**Interfaces:**
- Consumes: nothing new.
- Produces: `EpisodicMemory.search(...) -> list[dict]` where each dict is `{"content": str, "metadata": dict, "score": float}` (lower = closer under ChromaDB default L2). Consumed by Task 4's similarity filter (the results flow through `search_episodic_with_cache` → orchestrator → `assemble_context`).

- [ ] **Step 1: Write the failing test**

In `tests/test_episodic_queries.py`, extend `_FakeCollection` with a `query` method. Add to the `_FakeCollection` class body (after `delete`):
```python
    def query(self, query_texts=None, n_results=5, where=None):
        # Return closest-first with synthetic distances (lower = closer).
        docs = [e["content"] for e in self._entries][:n_results]
        metas = [e["metadata"] for e in self._entries][:n_results]
        dists = [0.2 * i for i in range(len(docs))]      # 0.0, 0.2, 0.4, ...
        return {"ids": [[e["id"] for e in self._entries[:n_results]]],
                "documents": [docs], "metadatas": [metas], "distances": [dists]}
```

Add this test at the end of `tests/test_episodic_queries.py`:
```python
def test_search_returns_score_closest_first():
    e = _episodic([
        _entry(0, "a", "close memory", 1.0),
        _entry(1, "a", "far memory", 2.0),
        _entry(2, "a", "furthest memory", 3.0),
    ])
    res = asyncio.run(e.search("close memory", limit=3))
    assert len(res) == 3
    assert res[0]["content"] == "close memory"
    assert "score" in res[0]
    assert res[0]["score"] == 0.0                       # closest -> lowest distance
    assert res[1]["score"] == 0.2
    assert res[2]["score"] == 0.4
    assert all("metadata" in r for r in res)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_episodic_queries.py::test_search_returns_score_closest_first -v`
Expected: FAIL with `KeyError: 'score'` (current `search` returns only `content`/`metadata`).

- [ ] **Step 3: Write minimal implementation**

In `memory/episodic/episodic.py`, replace the `search` method (lines 62-79) with:
```python
    async def search(
        self, query: str, limit: int = 5,
        after: float | None = None, before: float | None = None,
    ) -> list[dict]:
        """Semantic search with optional timestamp filter.

        Returns ``{"content", "metadata", "score"}`` dicts, closest first.
        ``score`` is ChromaDB's distance (lower = closer under the default L2
        metric); callers use it to similarity-filter L3 injection. A custom
        collection that omits distances degrades to ``score = 0.0`` (passes any
        similarity filter) rather than crashing the turn.
        """
        where: dict | None = None
        if after is not None and before is not None:
            where = {"$and": [{"timestamp": {"$gte": after}}, {"timestamp": {"$lte": before}}]}
        elif after is not None:
            where = {"timestamp": {"$gte": after}}
        elif before is not None:
            where = {"timestamp": {"$lte": before}}
        kw: dict = {"query_texts": [query], "n_results": limit}
        if where:
            kw["where"] = where
        results = await asyncio.to_thread(self._get_collection().query, **kw)
        docs = results["documents"][0]
        metas = results["metadatas"][0]
        dists = results.get("distances", [[]])[0]
        if len(dists) != len(docs):                      # defensive: degrade to 0.0
            dists = [0.0] * len(docs)
        return [
            {"content": d, "metadata": m, "score": s}
            for d, m, s in zip(docs, metas, dists)
        ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_episodic_queries.py -v`
Expected: PASS (all existing tests + the new search test)

- [ ] **Step 5: Commit**

```bash
git add memory/episodic/episodic.py tests/test_episodic_queries.py
git commit -m "feat(episodic): search returns ChromaDB distance as score"
```

---

## Task 4: `assemble_context` new split + post-search similarity filtering

**Files:**
- Modify: `memory/layers.py` (import line 20; `assemble_context` lines 220-240)
- Modify: `tests/test_context_budget.py` (add assemble tests; add `import asyncio` + `from memory.budget import estimate_tokens`)

**Interfaces:**
- Consumes: `allocate_context_budget` (Task 2, 2-arg), `L3_SIMILARITY_MAX_DISTANCE` (Task 1), and L3 results carrying `score` (Task 3).
- Produces: `assemble_context` unchanged signature `(chat_id, budget=None, l3_results=None) -> (blocks, l3_used)`, but `l3_used` now reflects similarity-filtered + token-fit results (so Task 7's `prefetch_blocks_used` reflects reality).

- [ ] **Step 1: Write the failing tests**

In `tests/test_context_budget.py`, add at the top with the other imports:
```python
import asyncio
from memory.budget import estimate_tokens
```

Add these helper fakes and tests at the end of `tests/test_context_budget.py`:
```python
# --- assemble_context: priority-inverted split + similarity filter ---------------

class _FakePermanent:
    async def get_all_facts(self):
        return {}


class _FakeWorking:
    def __init__(self, messages):
        self._m = list(messages)

    async def get_messages(self, chat_id):
        return list(self._m)

    async def save_messages(self, chat_id, messages):
        self._m = messages


def _layers(messages):
    return MemoryLayers(_FakeWorking(messages), episodic=None, permanent=_FakePermanent())


def _res(content, score):
    return {"content": content, "metadata": {"timestamp": 0.0}, "score": score}


def test_assemble_filters_l3_by_similarity():
    """Only results under the similarity threshold are injected."""
    layers = _layers([])                                # empty L2 -> L3 gets the budget
    results = [
        _res("relevant A", 0.4),                        # passes (<= 1.0)
        _res("irrelevant B", 2.5),                      # filtered out
        _res("relevant C", 0.9),                        # passes
    ]
    blocks, l3_used = asyncio.run(
        layers.assemble_context("c", budget=4000, l3_results=results)
    )
    related = [b for b in blocks if b.startswith("[Related Memory]")]
    assert len(related) == 1
    assert "relevant A" in related[0]
    assert "relevant C" in related[0]
    assert "irrelevant B" not in related[0]
    assert l3_used == 2


def test_assemble_l2_cap_reserves_l3_guarantee():
    """L2 fills its share but the L3 guarantee is held back (L2 < budget)."""
    big = [_msg("user", "x" * 400) for _ in range(50)]  # ~100 tok each
    layers = _layers(big)
    blocks, _ = asyncio.run(layers.assemble_context("c", budget=4000, l3_results=[]))
    history = [b for b in blocks if b.startswith("[Conversation History]")][0]
    tok = estimate_tokens(history)
    assert 2000 < tok < 3900                             # L2 got remainder MINUS the guarantee


def test_assemble_l3_guaranteed_when_l2_small():
    """Simple turn (tiny L2): L3 gets the full remainder, >= the guarantee."""
    layers = _layers([])                                # no L2 history
    results = [_res("only result", 0.3)]
    blocks, l3_used = asyncio.run(
        layers.assemble_context("c", budget=4000, l3_results=results)
    )
    assert l3_used == 1
    assert any(b.startswith("[Related Memory]") for b in blocks)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_context_budget.py -k assemble -v`
Expected: FAIL — `test_assemble_filters_l3_by_similarity` fails (no filtering today: all 3 results injected, `irrelevant B` present); `test_assemble_l2_cap_reserves_l3_guarantee` may fail (old code caps L2 at 8000/30% logic, not the guarantee).

- [ ] **Step 3: Write minimal implementation**

In `memory/layers.py`, change the config import (line 20) from:
```python
from memory.config import IDENTITY_BASE_PROMPT, MAX_CONTEXT_TOKENS
```
to:
```python
from memory.config import IDENTITY_BASE_PROMPT, L3_SIMILARITY_MAX_DISTANCE, MAX_CONTEXT_TOKENS
```

In `memory/layers.py`, replace the budget-split + L3 section of `assemble_context` (lines 220-240, from the `# Split the budget` comment through `return blocks, l3_used`) with:
```python
        # Split the budget: L3 guaranteed minimum first (priority-inverted); L2
        # gets the remainder and so stays AITS-scaled. L3 is never starved to 0.
        l2_cap, l3_guarantee = allocate_context_budget(mandatory_tokens, budget)
        # L2: working context — capped to its share, drop-oldest, never fully lost.
        messages = await self.get_working_context(chat_id)
        trimmed = self._trim_recent_messages(messages, l2_cap)
        l2_tokens = 0
        if trimmed:
            l2_block = f"[Conversation History]\n{self._format_messages(trimmed)}"
            l2_tokens = estimate_tokens(l2_block)
            blocks.append(l2_block)
        # L3: episodic — similarity-filtered, then fit the budget remaining after
        # L0+L1+L2 (>= l3_guarantee by construction, since l2_tokens <= l2_cap =
        # available - l3_guarantee). Relevance is decided by ChromaDB score, not
        # query form; search is unconditional (the orchestrator always runs it).
        l3_used = 0
        if l3_results:
            l3_budget = max(budget - mandatory_tokens - l2_tokens, 0)
            if l3_budget > 0:
                relevant = [
                    r for r in l3_results
                    if r.get("score", 0.0) <= L3_SIMILARITY_MAX_DISTANCE
                ]
                l3_block, l3_used = self._fit_search_results(relevant, l3_budget)
                if l3_block:
                    blocks.append(f"[Related Memory]\n{l3_block}")
        return blocks, l3_used
```
Leave `_trim_recent_messages`, `_fit_search_results`, `_format_facts`, `_format_messages` unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_context_budget.py -v`
Expected: PASS (all allocate, trim, and assemble tests)

- [ ] **Step 5: Commit**

```bash
git add memory/layers.py tests/test_context_budget.py
git commit -m "feat(memory): assemble_context priority-inverted split + similarity filtering"
```

---

## Task 5: Unconditional search + cache_key reporting

This task is atomic across three files because `search_episodic_with_cache` changes its return arity — the orchestrator unpack and the collector must change in the same commit or the build breaks.

**Files:**
- Modify: `memory/layers.py:144-162` (`search_episodic_with_cache`)
- Modify: `memory/observability_collector.py:78-81` (`set_cache`)
- Modify: `orchestrator/orchestrator.py:150-176` (search stage)
- Modify: `tests/_orch_fakes.py` (NEW), `tests/test_orchestrator_memory_flow.py` (NEW)

**Interfaces:**
- Consumes: `search_episodic_with_cache` currently returns `(results, cache_hit)`; the orchestrator unpacks 2.
- Produces: `search_episodic_with_cache -> (results, cache_hit, cache_key)`; `set_cache(hit, key=None)`; orchestrator always searches (no confidence gate); `MemoryObservation.cache_key` populated.

- [ ] **Step 1: Write the failing test**

Create `tests/_orch_fakes.py`:
```python
"""Shared fakes for orchestrator memory-flow tests (no real backends)."""
from __future__ import annotations

import asyncio


class _Message:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _Choice:
    def __init__(self, message):
        self.message = message


class _Response:
    def __init__(self, content="ok"):
        self.choices = [_Choice(_Message(content=content))]


class _Completions:
    def __init__(self, content="ok", delay=0.0):
        self._content = content
        self._delay = delay

    async def create(self, **kw):
        if self._delay:
            await asyncio.sleep(self._delay)
        return _Response(self._content)


class _Chat:
    def __init__(self, completions):
        self.completions = completions


class _LLMClient:
    def __init__(self, completions):
        self.chat = _Chat(completions)


class _FakeLayers:
    def __init__(self, results=None, blocks=None, l3_used=0):
        self._results = results or []
        self._blocks = blocks or ["[Identity]\nYou are GOAT."]
        self._l3_used = l3_used
        self.search_calls = 0
        self.last_query = None

    async def search_episodic_with_cache(self, chat_id, query, limit=5):
        self.search_calls += 1
        self.last_query = query
        return list(self._results), False, "search:deadbeef"

    async def assemble_context(self, chat_id, budget=None, l3_results=None):
        return list(self._blocks), self._l3_used

    async def get_working_context(self, chat_id):
        return []

    async def save_working_context(self, chat_id, messages):
        self.saved = messages


class _FakeAnalytics:
    def __init__(self):
        self.total_requests = 0
        self.records = []

    def record(self, obs):
        self.records.append(obs)
        self.total_requests += 1

    def log_report(self):
        pass


class _FakePluginManager:
    tools = []


class _FakeRegistry:
    def __init__(self, layers, llm, analytics):
        self.memory_layers = layers
        self.llm_client = llm
        self.memory_analytics = analytics
        self.plugin_manager = _FakePluginManager()
```

Create `tests/test_orchestrator_memory_flow.py`:
```python
"""tests.test_orchestrator_memory_flow — unconditional search + cache_key reporting."""
from __future__ import annotations

import asyncio

from orchestrator.orchestrator import Orchestrator
from tests._orch_fakes import (
    _Completions, _FakeAnalytics, _FakeLayers, _FakeRegistry, _LLMClient,
)


def test_search_runs_unconditionally_and_reports_cache_key():
    """Intent containing 'la' used to drop confidence to 0.2 and skip search."""
    intent = "Pai și după atâtea tokens prefetchul a folosit 0 blocks la fiecare qwery"
    layers = _FakeLayers(results=[{"content": "m", "metadata": {"timestamp": 0.0}, "score": 0.5}])
    reg = _FakeRegistry(layers, _LLMClient(_Completions("reply")), _FakeAnalytics())
    reply = asyncio.run(Orchestrator(reg, tools=[]).run(intent, "chat"))
    assert layers.search_calls == 1                # search ran despite low confidence
    assert reply == "reply"
    obs = reg.memory_analytics.records[-1]
    assert obs.cache_key == "search:deadbeef"       # cache key now reported (was null)
    assert obs.prefetch_attempted is True
    assert obs.prefetch_succeeded is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_orchestrator_memory_flow.py -v`
Expected: FAIL — `search_calls == 0` (the confidence gate skips search for the "la" intent) and `obs.cache_key is None` (key not propagated).

- [ ] **Step 3: Write minimal implementation**

In `memory/layers.py`, replace `search_episodic_with_cache` (lines 144-162) with:
```python
    async def search_episodic_with_cache(
        self, chat_id: str, query: str, limit: int = 5,
    ) -> tuple[list[dict], bool, str]:
        """L3 + L2.5: semantic search, served from the session cache on repeat.

        Returns ``(results, cache_hit, cache_key)``: the results, whether they
        came from the cache, and the deterministic key (so the orchestrator can
        report it in observability). Key is ``search:{sha256(query)[:16]}`` —
        SHA-256 (not Python's randomised hash) for cross-restart stability.
        Results capped to ``MAX_RESULTS_PER_SEARCH`` before caching so cache
        hits need no re-cap.
        """
        cache_key = self._search_cache_key(query)
        cached = await self._cache.get(chat_id, cache_key)
        if cached is not None:
            return cached["results"], True, cache_key
        log.debug("episodic search (cache miss) chat=%s query=%r", chat_id, query[:80])
        results = enforce_result_limit(await self._episodic.search(query, limit=limit))
        await self._cache.set(chat_id, cache_key, {"results": results})
        return results, False, cache_key
```

In `memory/observability_collector.py`, replace `set_cache` (lines 78-81) with:
```python
    def set_cache(self, hit: bool, key: str | None = None) -> None:
        """Set the L2.5 cache outcome (hit/miss) and report the cache key.

        The key was previously treated as internal and never stored; it is now
        propagated so the observability record shows what was actually looked up.
        """
        self.obs.cache_hit = hit
        self.obs.cache_miss = not hit
        self.obs.cache_key = key
```

In `orchestrator/orchestrator.py`, replace the search stage (lines 150-176, from `# 2. Bounded-time L3 prefetch` through the `collector.set_prefetch(...)` line) with:
```python
            # 2. Unconditional L3 search (search stage, non-blocking, bounded time).
            #    Pre-search confidence gating is removed — every turn searches;
            #    relevance is decided post-search by similarity score (assemble),
            #    not by query form. The search_memory tool remains the on-demand
            #    fallback. latency_search (~0.2s, local embedding) is negligible
            #    next to the LLM call in the inject/llm stage.
            collector.start_stage("search")
            l3_results: list[dict] = []
            cache_hit = False
            cache_key: str | None = None
            prefetch_attempted = True
            prefetch_succeeded = False
            prefetch_timeout = False
            try:
                l3_results, cache_hit, cache_key = await asyncio.wait_for(
                    layers.search_episodic_with_cache(chat_id, intent, limit=MAX_RESULTS_PER_SEARCH),
                    timeout=PREFETCH_TIMEOUT,
                )
                prefetch_succeeded = True
                log.info("episodic search ok chat=%s hits=%d", chat_id, len(l3_results))
            except asyncio.TimeoutError:
                prefetch_timeout = True
                log.warning("episodic search timed out chat=%s, continuing without L3", chat_id)
            except Exception as exc:
                log.warning("episodic search failed chat=%s: %s, continuing without L3", chat_id, exc)
            collector.end_stage("search")
            collector.set_cache(cache_hit, cache_key)
            collector.set_prefetch(prefetch_attempted, prefetch_succeeded, prefetch_timeout, len(l3_results), 0)
```
Note: keep the `PREFETCH_CONFIDENCE_THRESHOLD` import — it is still used by `categorize_intent` at the classify stage (line 143) for the observability intent label. Only the gate is removed.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_orchestrator_memory_flow.py -v && pytest tests/ -v`
Expected: PASS (new test + no regressions across the suite)

- [ ] **Step 5: Commit**

```bash
git add memory/layers.py memory/observability_collector.py orchestrator/orchestrator.py tests/_orch_fakes.py tests/test_orchestrator_memory_flow.py
git commit -m "feat(orchestrator): unconditional L3 search + report cache_key"
```

---

## Task 6: Wire `prefetch_blocks_used` to the real `l3_used`

**Files:**
- Modify: `memory/observability_collector.py` (add `set_prefetch_blocks_used`)
- Modify: `orchestrator/orchestrator.py:177-181` (call the setter after assemble)
- Modify: `tests/test_orchestrator_memory_flow.py` (add a test)

**Interfaces:**
- Consumes: `l3_used` returned by `assemble_context` (already available at orchestrator line 179).
- Produces: `ObservationCollector.set_prefetch_blocks_used(blocks_used: int)`; `MemoryObservation.prefetch_blocks_used` reflects reality (was hardcoded `0`).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_orchestrator_memory_flow.py`:
```python
def test_prefetch_blocks_used_reflects_real_l3_used():
    """The hardcoded 0 is replaced by the actual count assembled into context."""
    layers = _FakeLayers(
        results=[{"content": "m", "metadata": {"timestamp": 0.0}, "score": 0.5}],
        l3_used=3,
    )
    reg = _FakeRegistry(layers, _LLMClient(_Completions("r")), _FakeAnalytics())
    asyncio.run(Orchestrator(reg, tools=[]).run("what is X", "c"))
    obs = reg.memory_analytics.records[-1]
    assert obs.prefetch_blocks_used == 3            # no longer 0
    assert obs.results_used == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_orchestrator_memory_flow.py::test_prefetch_blocks_used_reflects_real_l3_used -v`
Expected: FAIL — `obs.prefetch_blocks_used == 0` (hardcoded at orchestrator line 176).

- [ ] **Step 3: Write minimal implementation**

In `memory/observability_collector.py`, add this method (after `set_prefetch`, ~line 93):
```python
    def set_prefetch_blocks_used(self, blocks_used: int) -> None:
        """Patch in the real L3 usage count once assemble_context has run.

        ``set_prefetch`` is called before assemble (only the raw result count is
        known then); this sets ``prefetch_blocks_used`` to the ``l3_used`` value
        ``assemble_context`` returns, replacing the placeholder 0.
        """
        self.obs.prefetch_blocks_used = blocks_used
```

In `orchestrator/orchestrator.py`, after the assemble stage block (after the existing `collector.set_context_from_blocks(...)` line, currently ~line 181), add:
```python
            collector.set_prefetch_blocks_used(l3_used)
```
So the assemble stage block reads:
```python
            collector.start_stage("assemble")
            context_blocks, l3_used = await layers.assemble_context(chat_id, budget=budget, l3_results=l3_results)
            collector.end_stage("assemble")
            collector.set_context_from_blocks(context_blocks, results_found=len(l3_results), results_used=l3_used)
            collector.set_prefetch_blocks_used(l3_used)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_orchestrator_memory_flow.py -v`
Expected: PASS (both tests)

- [ ] **Step 5: Commit**

```bash
git add memory/observability_collector.py orchestrator/orchestrator.py tests/test_orchestrator_memory_flow.py
git commit -m "fix(observability): wire prefetch_blocks_used to real l3_used"
```

---

## Task 7: Split `latency_inject` into inject / llm / save

The "inject" stage currently bundles prompt-build + the ~30s LLM API call + L2 persist, mislabeling external API time as memory injection. Split it so the LLM time is honestly attributed.

**Files:**
- Modify: `memory/observability.py:50-55` (add fields)
- Modify: `orchestrator/orchestrator.py:182-227` (split the stage)
- Modify: `memory/analytics.py` (`_reset`, `record`, `get_report`)
- Modify: `tests/test_orchestrator_memory_flow.py` (add a test)

**Interfaces:**
- Consumes: nothing new.
- Produces: `MemoryObservation.latency_llm`, `MemoryObservation.latency_save` fields; analytics `avg_latency_llm`, `avg_latency_save`. `latency_inject` is redefined to prompt-assembly only.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_orchestrator_memory_flow.py`:
```python
def test_latency_split_llm_vs_inject():
    """The LLM call is isolated in latency_llm; inject is just prompt build."""
    layers = _FakeLayers(results=[])
    # 20ms LLM call so latency_llm is measurably the dominant stage.
    reg = _FakeRegistry(layers, _LLMClient(_Completions("r", delay=0.02)), _FakeAnalytics())
    asyncio.run(Orchestrator(reg, tools=[]).run("hello", "c"))
    obs = reg.memory_analytics.records[-1]
    assert obs.latency_llm >= 0.015                 # the LLM call, isolated
    assert obs.latency_inject < 0.01                # prompt build only — no longer the 30s
    assert obs.latency_save >= 0.0
    assert obs.latency_llm + obs.latency_inject + obs.latency_save <= obs.latency_total + 0.05
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_orchestrator_memory_flow.py::test_latency_split_llm_vs_inject -v`
Expected: FAIL — `AttributeError: 'MemoryObservation' object has no attribute 'latency_llm'` (the stage timer uses `setattr`, but the field must exist on the dataclass for it to serialize/be readable; and the orchestrator hasn't split the stage yet, so `latency_inject` still contains the 20ms).

- [ ] **Step 3: Write minimal implementation**

In `memory/observability.py`, replace the latency fields block (lines 50-55):
```python
    # Latency (seconds) per stage
    latency_classify: float = 0.0
    latency_search: float = 0.0
    latency_assemble: float = 0.0
    latency_inject: float = 0.0
    latency_total: float = 0.0
```
with:
```python
    # Latency (seconds) per stage. inject = prompt assembly only; llm holds the
    # (dominant) LLM API call(s); save = L2 working-memory persist.
    latency_classify: float = 0.0
    latency_search: float = 0.0
    latency_assemble: float = 0.0
    latency_inject: float = 0.0
    latency_llm: float = 0.0
    latency_save: float = 0.0
    latency_total: float = 0.0
```

In `memory/analytics.py` `_reset` (after `self.total_latency_inject = 0.0`, line 49), add:
```python
        self.total_latency_llm = 0.0
        self.total_latency_save = 0.0
```
In `memory/analytics.py` `record` (after `self.total_latency_inject += obs.latency_inject`, line 78), add:
```python
        self.total_latency_llm += obs.latency_llm
        self.total_latency_save += obs.latency_save
```
In `memory/analytics.py` `get_report` (after `"avg_latency_inject": self.total_latency_inject / n,`, line 103), add:
```python
            "avg_latency_llm": self.total_latency_llm / n,
            "avg_latency_save": self.total_latency_save / n,
```

In `orchestrator/orchestrator.py`, replace the inject stage (lines 182-227, from `# 4-5. Build prompt + LLM call + tool round (inject stage).` through `collector.end_stage("inject")`) with:
```python
            # 4. Build the prompt (inject stage — small, just assembly).
            collector.start_stage("inject")
            system_content = "\n\n".join(context_blocks)
            if self._has_search_memory():
                system_content += f"\n\n{_SEARCH_MEMORY_GUIDANCE}"
            if self._has_store_memory():
                system_content += f"\n\n{_STORE_MEMORY_GUIDANCE}"
            if self._has_tool("promote_memory"):
                system_content += f"\n\n{_PROMOTE_MEMORY_GUIDANCE}"
            if self._has_tool("get_memory_metrics") or self._has_tool("get_recent_logs"):
                system_content += f"\n\n{_INTROSPECTION_GUIDANCE}"
            api_msgs = [
                {"role": "system", "content": system_content},
                {"role": "user", "content": intent},
            ]
            all_tools = self._all_tools()
            kw: dict = dict(model=settings.MODEL_NAME, messages=api_msgs,
                            temperature=settings.TEMPERATURE, max_tokens=settings.MAX_TOKENS)
            if all_tools:
                kw["tools"] = [t.to_openai_schema() for t in all_tools]
                kw["tool_choice"] = "auto"
            collector.end_stage("inject")
            # 5. LLM call + tool round (llm stage — the dominant latency).
            collector.start_stage("llm")
            response = await self._registry.llm_client.chat.completions.create(**kw)
            choice = response.choices[0]
            content = choice.message.content or ""
            log.debug(
                "LLM response chat=%s tool_calls=%s content_hex=%s",
                chat_id,
                bool(choice.message.tool_calls),
                content[:80].encode("unicode_escape").decode(),
            )
            if choice.message.tool_calls:
                reply = await self._run_tool_round(api_msgs, choice, chat_id, all_tools)
                if _DSML_BLOCK.search(reply):
                    log.warning("DSML in _run_tool_round reply chat=%s; running DSML round", chat_id)
                    reply = await self._run_dsml_tool_round(reply, chat_id, all_tools)
            elif _DSML_BLOCK.search(content):
                log.warning("DSML tool calls in content (model=%s); running DSML round", settings.MODEL_NAME)
                reply = await self._run_dsml_tool_round(content, chat_id, all_tools)
            else:
                reply = content
            collector.end_stage("llm")
            # 6. Persist this turn (save stage).
            collector.start_stage("save")
            messages = await layers.get_working_context(chat_id)
            messages.append({"role": "user", "content": intent, "timestamp": time.time()})
            messages.append({"role": "assistant", "content": reply, "timestamp": time.time()})
            await layers.save_working_context(chat_id, messages)
            collector.end_stage("save")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_orchestrator_memory_flow.py -v && pytest tests/test_introspection_plugins.py tests/test_plugin_manager.py tests/test_promote.py -v`
Expected: PASS (latency split test + no regressions in the rest of the suite)

- [ ] **Step 5: Commit**

```bash
git add memory/observability.py memory/analytics.py orchestrator/orchestrator.py tests/test_orchestrator_memory_flow.py
git commit -m "fix(observability): split latency_inject into inject/llm/save"
```

---

## Verification (post-implementation, per spec §Verification)

After all 7 tasks are green, run these checks (not automated tests — empirical confirmation against real logs):

- [ ] **V1 — Search latency at volume:** benchmark `EpisodicMemory.search` at the current collection size and at 5×/10× (synthesize entries). Confirm `latency_search < 0.5s` (supports unconditional search being negligible vs the LLM call). If it fails, revisit §3 of the spec.
- [ ] **V2 — L3 never starves + floor tuning:** run recall / simple / min-budget turns; confirm `tokens_l3 >= L3_MIN_GUARANTEE_TOKENS` whenever L3 results pass the similarity filter (never 0), `tokens_l3 = budget − L0/L1 − tokens_l2_actual` when L3 fills, `tokens_l2 <= budget − L0/L1 − L3_MIN_GUARANTEE_TOKENS`, and on the min-budget (~2800) turn `tokens_l2 >= L2_FLOOR_TOKENS`. Then sweep `L3_MIN_GUARANTEE_TOKENS` over 800–1400 and pick the value that maximizes relevant-L3-injected without breaching the L2 floor on the min-budget turn; update `config/memory.toml`.
- [ ] **V3 — Similarity threshold calibration:** build a small labeled set of (query, stored memory, relevant/irrelevant) pairs from real logs (include the "salut azi" recall counterexample). Sweep `L3_SIMILARITY_MAX_DISTANCE` and pick the value maximizing relevant-kept / irrelevant-dropped; update `config/memory.toml`.
- [ ] **V4 — Metric honesty:** confirm on a real recall turn that `prefetch_blocks_used == results_used == l3_used` (nonzero), `cache_key` is non-null, `latency_llm` carries the ~9-30s and `latency_inject` is small.

---

## Self-Review notes

- **Spec coverage:** §1–2 (priority-inverted split) → Tasks 1,2,4. §3 (unconditional search) → Task 5. §4 (similarity filter) → Tasks 1,3,4. §5 (wire metrics + split latency) → Tasks 5,6,7. Deferred cache keying → no task (intentional). Verification V1–V4 covers the spec's §Verification.
- **Type consistency:** `allocate_context_budget(mandatory_tokens, budget) -> (l2_cap, l3_guarantee)` (Task 2) matches the call in Task 4 (`l2_cap, l3_guarantee = allocate_context_budget(mandatory_tokens, budget)`). `search_episodic_with_cache -> (results, cache_hit, cache_key)` (Task 5) matches the orchestrator unpack and the `_FakeLayers` fake. `set_cache(hit, key=None)` (Task 5) matches `collector.set_cache(cache_hit, cache_key)`. `set_prefetch_blocks_used(l3_used)` (Task 6) matches the call with `l3_used` from assemble.
- **No placeholders:** every code step contains the full code; numeric defaults (1200, 1.0) are concrete, with empirical tuning deferred to V2/V3 (calibration, not implementation gaps).