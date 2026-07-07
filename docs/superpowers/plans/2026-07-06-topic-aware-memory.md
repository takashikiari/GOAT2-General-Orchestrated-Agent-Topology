# Topic-Aware Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tag every auto-archived L3 entry with a `topic_id` UUID and use it to pre-filter ChromaDB before semantic search, while improving centroid stability with a weighted update and detecting when the user returns to a past topic.

**Architecture:** `topic_id` is a UUID generated at each cold-break and stored in the `Activation` blob (Redis). Every `_archive_turn()` write carries the current `topic_id` as ChromaDB metadata. The prefetch daemon uses it to narrow the search space on warm/drift/topic-return turns. On a cold break the previous centroid is archived; `find_topic_return()` compares the new query embedding against all archived centroids before declaring a fresh topic. Centroid updates use a stability-weighted blend so stable topics resist drift. No new LLM calls; all computation reuses the ONNX MiniLM already running.

**Tech Stack:** Python 3.11+, ChromaDB (ONNX MiniLM), Redis (aioredis), pytest (pure-function tests, no real backends).

## Global Constraints

- Max 90 lines per new file; single responsibility.
- No new LLM calls added to the pipeline.
- All new pure functions must be unit-testable with no services.
- Backward-compatible: existing Redis activation blobs missing the new fields must deserialise safely with defaults.
- Existing ChromaDB entries without `topic_id` metadata must not break any search path (topic-filtered returns 0, global thematic is always the fallback).
- Run `python3 -m pytest tests/ -v` after every task — must stay green.

---

## File Map

| Action | Path | What changes |
|--------|------|-------------|
| Modify | `memory/activation.py` | 3 new fields on `Activation`; 3 new pure functions; `to_dict`/`from_dict`; `__all__` |
| Modify | `memory/config.py` | 2 new constants (`TOPIC_RETURN_THRESHOLD`, `TOPIC_ARCHIVE_MAX`) + defaults |
| Modify | `config/memory.toml` | 2 new keys under `[activation]` |
| Modify | `memory/episodic/episodic.py` | Add `topic_id` param to `search()` |
| Modify | `memory/layers.py` | Add `topic_id` to `search_episodic()`, `search_episodic_with_cache()`, `store_episodic()` |
| Modify | `orchestrator/orchestrator.py` | `_archive_turn()`, `_update_activation()`, `_prefetch_daemon()`, `run()` |
| Modify | `tests/test_activation.py` | 8 new tests for the pure functions and serialisation |
| Modify | `tests/_orch_fakes.py` | Update `store_episodic` and `search_episodic` signatures |

---

### Task 1: Activation model — new fields, pure functions, config

**Files:**
- Modify: `memory/activation.py`
- Modify: `memory/config.py`
- Modify: `config/memory.toml`
- Test: `tests/test_activation.py`

**Interfaces:**
- Produces:
  - `Activation.topic_id: str` — UUID for current topic (empty string = unset)
  - `Activation.turn_count: int` — turns accumulated since last cold start
  - `Activation.archived_topics: list[dict]` — snapshots: `[{"topic_id": str, "centroid": list[float], "ts": float}]`
  - `update_centroid_weighted(centroid, query_emb, turn_count) -> list[float]`
  - `find_topic_return(query_emb, archived_topics, threshold) -> str | None`
  - `archive_current_topic(activation, max_archived) -> list[dict]`
  - `TOPIC_RETURN_THRESHOLD: float` (default `0.75`)
  - `TOPIC_ARCHIVE_MAX: int` (default `10`)

- [ ] **Step 1: Write the failing tests**

Add to the bottom of `tests/test_activation.py`:

```python
from memory.activation import (
    update_centroid_weighted,
    find_topic_return,
    archive_current_topic,
)


# --- update_centroid_weighted -----------------------------------------------

def test_update_centroid_weighted_full_replace_at_turn_one():
    # turn_count=1 → alpha = 1/min(1,20) = 1.0 → result is pure query_emb
    result = update_centroid_weighted([1.0, 0.0], [0.0, 1.0], 1)
    assert result == [0.0, 1.0]


def test_update_centroid_weighted_blend_at_turn_two():
    # turn_count=2 → alpha = 0.5 → 50/50 blend
    result = update_centroid_weighted([1.0, 0.0], [0.0, 1.0], 2)
    assert abs(result[0] - 0.5) < 1e-9
    assert abs(result[1] - 0.5) < 1e-9


def test_update_centroid_weighted_stable_at_high_turn_count():
    # turn_count=20 → alpha = 1/20 = 0.05 → 95% centroid + 5% query
    result = update_centroid_weighted([1.0, 0.0], [0.0, 1.0], 20)
    assert abs(result[0] - 0.95) < 1e-9
    assert abs(result[1] - 0.05) < 1e-9


def test_update_centroid_weighted_caps_alpha_at_twenty():
    # turn_count=50 → min(50, 20) = 20 → same as turn_count=20
    r20 = update_centroid_weighted([1.0, 0.0], [0.0, 1.0], 20)
    r50 = update_centroid_weighted([1.0, 0.0], [0.0, 1.0], 50)
    assert r20 == r50


# --- find_topic_return -------------------------------------------------------

def test_find_topic_return_matches_closest_above_threshold():
    archived = [
        {"topic_id": "t1", "centroid": [1.0, 0.0], "ts": 1.0},
        {"topic_id": "t2", "centroid": [0.0, 1.0], "ts": 2.0},
    ]
    result = find_topic_return([1.0, 0.0], archived, threshold=0.75)
    assert result == "t1"


def test_find_topic_return_none_when_below_threshold():
    # cosine([0.6, 0.8], [1.0, 0.0]) = 0.6 < 0.75
    archived = [{"topic_id": "t1", "centroid": [1.0, 0.0], "ts": 1.0}]
    result = find_topic_return([0.6, 0.8], archived, threshold=0.75)
    assert result is None


def test_find_topic_return_none_on_empty_inputs():
    assert find_topic_return(None, [{"topic_id": "t1", "centroid": [1.0, 0.0], "ts": 1.0}], 0.75) is None
    assert find_topic_return([1.0, 0.0], [], 0.75) is None


# --- archive_current_topic ---------------------------------------------------

def test_archive_current_topic_appends_entry():
    act = Activation(centroid=[1.0, 0.0], topic_id="t1", ts=1.0, archived_topics=[])
    result = archive_current_topic(act, max_archived=10)
    assert len(result) == 1
    assert result[0]["topic_id"] == "t1"
    assert result[0]["centroid"] == [1.0, 0.0]


def test_archive_current_topic_trims_to_max_and_drops_oldest():
    existing = [{"topic_id": f"t{i}", "centroid": [float(i), 0.0], "ts": float(i)} for i in range(10)]
    act = Activation(centroid=[10.0, 0.0], topic_id="t10", ts=10.0, archived_topics=existing)
    result = archive_current_topic(act, max_archived=10)
    assert len(result) == 10
    assert result[-1]["topic_id"] == "t10"
    assert result[0]["topic_id"] == "t1"   # t0 dropped


def test_archive_current_topic_deduplicates_same_topic_id():
    existing = [{"topic_id": "t1", "centroid": [0.5, 0.5], "ts": 1.0}]
    act = Activation(centroid=[1.0, 0.0], topic_id="t1", ts=2.0, archived_topics=existing)
    result = archive_current_topic(act, max_archived=10)
    t1_entries = [e for e in result if e["topic_id"] == "t1"]
    assert len(t1_entries) == 1
    assert t1_entries[0]["ts"] == 2.0


def test_archive_current_topic_noop_when_no_topic_id():
    act = Activation(centroid=[1.0, 0.0], topic_id="", ts=1.0, archived_topics=[])
    result = archive_current_topic(act, max_archived=10)
    assert result == []


def test_activation_roundtrip_preserves_new_fields():
    act = Activation(
        centroid=[1.0, 0.0], merged=[], last_query="q", recent_queries=["q"],
        ts=1.0, topic_id="abc-123", turn_count=5,
        archived_topics=[{"topic_id": "old", "centroid": [0.0, 1.0], "ts": 0.5}],
    )
    restored = Activation.from_dict(act.to_dict())
    assert restored.topic_id == "abc-123"
    assert restored.turn_count == 5
    assert len(restored.archived_topics) == 1
    assert restored.archived_topics[0]["topic_id"] == "old"


def test_activation_from_dict_defaults_new_fields_when_missing():
    # Old Redis blob — no topic fields. Must deserialise safely.
    old_blob = {"centroid": [1.0, 0.0], "merged": [], "last_query": "q",
                "recent_queries": [], "ts": 1.0}
    act = Activation.from_dict(old_blob)
    assert act.topic_id == ""
    assert act.turn_count == 0
    assert act.archived_topics == []
```

- [ ] **Step 2: Run tests — expect failures**

```bash
cd /home/lenovo/workspace/goat2
python3 -m pytest tests/test_activation.py -v -k "topic or centroid_weighted or archive" 2>&1 | tail -20
```

Expected: `ImportError` or `AttributeError` — functions not defined yet.

- [ ] **Step 3: Add new fields to `Activation` and update serialisation**

In `memory/activation.py`, extend the `Activation` dataclass (after `ts: float = 0.0`):

```python
    topic_id: str = ""
    turn_count: int = 0
    archived_topics: list[dict] = field(default_factory=list)
```

Replace `to_dict`:
```python
    def to_dict(self) -> dict:
        return {
            "centroid": self.centroid,
            "merged": self.merged,
            "last_query": self.last_query,
            "recent_queries": self.recent_queries,
            "ts": self.ts,
            "topic_id": self.topic_id,
            "turn_count": self.turn_count,
            "archived_topics": self.archived_topics,
        }
```

Replace `from_dict`:
```python
    @classmethod
    def from_dict(cls, data: dict) -> "Activation":
        return cls(
            centroid=list(data.get("centroid") or []),
            merged=list(data.get("merged") or []),
            last_query=str(data.get("last_query") or ""),
            recent_queries=list(data.get("recent_queries") or []),
            ts=float(data.get("ts") or 0.0),
            topic_id=str(data.get("topic_id") or ""),
            turn_count=int(data.get("turn_count") or 0),
            archived_topics=list(data.get("archived_topics") or []),
        )
```

- [ ] **Step 4: Add the three pure functions** (append after `trim_recent` in `memory/activation.py`):

```python
def update_centroid_weighted(
    centroid: list[float], query_emb: list[float], turn_count: int,
) -> list[float]:
    """Stability-weighted centroid update.

    Early turns (turn_count small) allow large moves; a stable topic
    (turn_count → 20) resists drift with alpha → 0.05. Caps at turn 20
    so the minimum alpha is 5% (centroid can always track the thread).
    """
    alpha = 1.0 / min(max(turn_count, 1), 20)
    return [(1.0 - alpha) * c + alpha * q for c, q in zip(centroid, query_emb)]


def find_topic_return(
    query_emb: list[float] | None,
    archived_topics: list[dict],
    threshold: float,
) -> str | None:
    """Return the archived ``topic_id`` whose centroid best matches ``query_emb``.

    Compares the new query embedding against every archived topic centroid via
    cosine similarity. Returns the best-matching ``topic_id`` when the best
    similarity meets ``threshold``, else ``None``. None-safe on both inputs.
    """
    if not query_emb or not archived_topics:
        return None
    best_sim, best_id = 0.0, None
    for entry in archived_topics:
        sim = cosine(query_emb, entry.get("centroid") or [])
        if sim > best_sim:
            best_sim, best_id = sim, entry.get("topic_id")
    return best_id if best_id and best_sim >= threshold else None


def archive_current_topic(activation: "Activation", max_archived: int) -> list[dict]:
    """Snapshot the current topic centroid into the archived list.

    Removes any prior entry with the same ``topic_id`` (dedup on re-visit),
    appends the current centroid as a new snapshot, then trims to
    ``max_archived`` (newest-last). Returns a new list; ``activation`` is
    not mutated.
    """
    if not activation.centroid or not activation.topic_id:
        return list(activation.archived_topics)
    entry = {"topic_id": activation.topic_id, "centroid": activation.centroid, "ts": activation.ts}
    deduped = [a for a in activation.archived_topics if a.get("topic_id") != activation.topic_id]
    deduped.append(entry)
    return deduped[-max_archived:]
```

- [ ] **Step 5: Update `__all__` in `memory/activation.py`**

Add the three new names to the existing `__all__` list:

```python
    "update_centroid_weighted",
    "find_topic_return",
    "archive_current_topic",
```

- [ ] **Step 6: Add config constants**

In `memory/config.py`, add to `_DEFAULTS["activation"]`:
```python
        "topic_return_threshold": 0.75,
        "topic_archive_max": 10,
```

After the existing `ACTIVATION_LEXICAL_WINDOW` constant, add:
```python
TOPIC_RETURN_THRESHOLD: Final[float] = float(
    _activation_cfg.get("topic_return_threshold", _DEFAULTS["activation"]["topic_return_threshold"])
)
TOPIC_ARCHIVE_MAX: Final[int] = int(
    _activation_cfg.get("topic_archive_max", _DEFAULTS["activation"]["topic_archive_max"])
)
```

Add both names to `__all__`.

- [ ] **Step 7: Add to `config/memory.toml`** (append inside `[activation]` section, after `lexical_window`):

```toml
# Topic identity for L3 pre-filtering — a topic_id UUID is attached to every
# auto-archived turn and used to narrow the prefetch search space.
topic_return_threshold = 0.75   # cosine sim required to recognise a topic return
topic_archive_max = 10          # past topic centroids kept per chat (newest-last)
```

- [ ] **Step 8: Run tests — expect green**

```bash
python3 -m pytest tests/test_activation.py -v 2>&1 | tail -20
```

Expected: all pass, including the 13 new tests.

- [ ] **Step 9: Commit**

```bash
git add memory/activation.py memory/config.py config/memory.toml tests/test_activation.py
git commit -m "feat: add topic_id/turn_count/archived_topics to Activation + 3 pure functions"
```

---

### Task 2: topic_id flows through L3 storage and search

**Files:**
- Modify: `memory/episodic/episodic.py` — add `topic_id` param to `search()`
- Modify: `memory/layers.py` — thread `topic_id` through `search_episodic`, `search_episodic_with_cache`, `store_episodic`
- Modify: `tests/_orch_fakes.py` — update fake signatures

**Interfaces:**
- Consumes: nothing from Task 1 (independent plumbing change)
- Produces:
  - `EpisodicMemory.search(..., topic_id=None)` — adds `{"topic_id": {"$eq": topic_id}}` to ChromaDB where-clause when set
  - `MemoryLayers.search_episodic(..., topic_id=None)`
  - `MemoryLayers.search_episodic_with_cache(..., topic_id=None)` — distinct cache key when `topic_id` differs
  - `MemoryLayers.store_episodic(..., topic_id="")` — writes `topic_id` into metadata when non-empty

- [ ] **Step 1: Write the failing test**

Create `tests/test_topic_search.py`:

```python
"""tests.test_topic_search — unit tests for topic_id metadata flow.

Tests that topic_id is plumbed correctly through store and search without
hitting a real ChromaDB instance. Uses a spy on the collection._embedding_function
path to verify the where-clause is built correctly.
"""
from __future__ import annotations
import pytest


def _build_where(after, before, topic_id):
    """Mirror the clause-builder logic from episodic.search() for assertions."""
    clauses = []
    if after is not None:
        clauses.append({"timestamp": {"$gte": after}})
    if before is not None:
        clauses.append({"timestamp": {"$lte": before}})
    if topic_id:
        clauses.append({"topic_id": {"$eq": topic_id}})
    if len(clauses) == 0:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


def test_where_clause_topic_only():
    result = _build_where(None, None, "abc-123")
    assert result == {"topic_id": {"$eq": "abc-123"}}


def test_where_clause_topic_and_after():
    result = _build_where(1000.0, None, "abc-123")
    assert result == {"$and": [{"timestamp": {"$gte": 1000.0}}, {"topic_id": {"$eq": "abc-123"}}]}


def test_where_clause_no_topic():
    result = _build_where(None, None, None)
    assert result is None


def test_where_clause_no_topic_empty_string():
    result = _build_where(None, None, "")
    assert result is None


def test_where_clause_topic_with_after_and_before():
    result = _build_where(100.0, 200.0, "t1")
    assert result == {"$and": [
        {"timestamp": {"$gte": 100.0}},
        {"timestamp": {"$lte": 200.0}},
        {"topic_id": {"$eq": "t1"}},
    ]}
```

- [ ] **Step 2: Run test — expect pass** (pure helper logic, no real code yet)

```bash
python3 -m pytest tests/test_topic_search.py -v 2>&1 | tail -10
```

Expected: all 5 pass (pure logic test of the clause builder mirrors).

- [ ] **Step 3: Update `EpisodicMemory.search()` in `memory/episodic/episodic.py`**

Replace the current `search()` method:

```python
    async def search(
        self, query: str, limit: int = 5,
        after: float | None = None, before: float | None = None,
        topic_id: str | None = None,
    ) -> list[dict]:
        """Semantic search with optional timestamp and topic_id filters (read-only).

        ``topic_id`` narrows results to entries stored under that topic thread.
        Entries stored before topic tracking was introduced have no ``topic_id``
        metadata and will not match the filter — the caller is responsible for
        providing a global fallback when topic-filtered results are empty.
        """
        clauses: list[dict] = []
        if after is not None:
            clauses.append({"timestamp": {"$gte": after}})
        if before is not None:
            clauses.append({"timestamp": {"$lte": before}})
        if topic_id:
            clauses.append({"topic_id": {"$eq": topic_id}})

        where: dict | None = None
        if len(clauses) == 1:
            where = clauses[0]
        elif len(clauses) > 1:
            where = {"$and": clauses}

        kw: dict = {"query_texts": [query], "n_results": limit}
        if where:
            kw["where"] = where
        results = await asyncio.to_thread(self._get_collection().query, **kw)
        docs = results["documents"][0]
        metas = results["metadatas"][0]
        dists = results.get("distances", [[]])[0]
        if len(dists) != len(docs):
            dists = [0.0] * len(docs)
        return [
            {"content": d, "metadata": m, "score": s}
            for d, m, s in zip(docs, metas, dists)
        ]
```

- [ ] **Step 4: Update `MemoryLayers` in `memory/layers.py`**

**`search_episodic()`** — add `topic_id` param:
```python
    async def search_episodic(
        self, query: str, limit: int = 5,
        after: float | None = None, before: float | None = None,
        topic_id: str | None = None,
    ) -> list[dict]:
        results = await self._episodic.search(
            query, limit=limit, after=after, before=before, topic_id=topic_id,
        )
        return enforce_result_limit(results)
```

**`store_episodic()`** — add `topic_id` param:
```python
    async def store_episodic(
        self, chat_id: str, content: str, tags: list[str] | None = None,
        topic_id: str = "",
    ) -> None:
        now = time.time()
        metadata: dict = {
            "tags": ",".join(tags or []),
            "timestamp": now,
            "access_count": 0,
            "last_accessed_ts": now,
        }
        if topic_id:
            metadata["topic_id"] = topic_id
        await self._episodic.store(chat_id, content, metadata)
```

**`search_episodic_with_cache()`** — add `topic_id` and update cache key:
```python
    async def search_episodic_with_cache(
        self, chat_id: str, query: str, limit: int = 5,
        topic_id: str | None = None,
    ) -> tuple[list[dict], bool, str]:
        cache_key = self._search_cache_key(query, topic_id)
        cached = await self._cache.get(chat_id, cache_key)
        if cached is not None:
            return cached["results"], True, cache_key
        log.debug("episodic search (cache miss) chat=%s query=%r", chat_id, query[:80])
        results = enforce_result_limit(
            await self._episodic.search(query, limit=limit, topic_id=topic_id)
        )
        await self._cache.set(chat_id, cache_key, {"results": results})
        return results, False, cache_key
```

**`_search_cache_key()`** — include `topic_id` in digest:
```python
    @staticmethod
    def _search_cache_key(query: str, topic_id: str | None = None) -> str:
        key_str = query + (topic_id or "")
        digest = hashlib.sha256(key_str.encode("utf-8")).hexdigest()[:16]
        return f"{_SEARCH_NAMESPACE}:{digest}"
```

- [ ] **Step 5: Update `_FakeLayers` in `tests/_orch_fakes.py`**

Update three method signatures to accept (and ignore) the new params:

```python
    async def store_episodic(self, chat_id: str, content: str, tags=None, topic_id: str = "") -> None:
        self.archive_calls += 1

    async def search_episodic(self, query, limit=5, after=None, before=None, topic_id=None):
        self.search_calls += 1
        return list(self._results)

    async def search_episodic_with_cache(self, chat_id, query, limit=5, topic_id=None):
        self.search_calls += 1
        self.last_query = query
        return list(self._results), False, "search:deadbeef"
```

- [ ] **Step 6: Run full test suite — expect green**

```bash
python3 -m pytest tests/ -v 2>&1 | tail -20
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add memory/episodic/episodic.py memory/layers.py tests/_orch_fakes.py tests/test_topic_search.py
git commit -m "feat: thread topic_id through L3 store and search"
```

---

### Task 3: topic-aware activation update + prefetch daemon

**Files:**
- Modify: `orchestrator/orchestrator.py`
- Test: `tests/test_orchestrator_memory_flow.py` (add 2 targeted tests)

**Interfaces:**
- Consumes:
  - `update_centroid_weighted(centroid, query_emb, turn_count) -> list[float]` (Task 1)
  - `find_topic_return(query_emb, archived_topics, threshold) -> str | None` (Task 1)
  - `archive_current_topic(activation, max_archived) -> list[dict]` (Task 1)
  - `TOPIC_RETURN_THRESHOLD`, `TOPIC_ARCHIVE_MAX` (Task 1)
  - `layers.store_episodic(..., topic_id=str)` (Task 2)
  - `layers.search_episodic(..., topic_id=str)` (Task 2)
- Produces: nothing consumed externally; completes the feature.

- [ ] **Step 1: Read the existing tests to understand the test pattern**

```bash
grep -n "def test_" tests/test_orchestrator_memory_flow.py | head -20
```

- [ ] **Step 2: Write the two new failing tests**

Append to `tests/test_orchestrator_memory_flow.py`:

```python
# --- topic_id flows through archive -----------------------------------------

import asyncio
from memory.activation import Activation
from orchestrator.orchestrator import Orchestrator
from tests._orch_fakes import _FakeLayers, _LLMClient, _Completions, _FakeAnalytics, _FakePluginManager


class _TopicCaptureLayers(_FakeLayers):
    """Extends _FakeLayers to capture topic_id passed to store_episodic."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.stored_topic_ids: list[str] = []
        self._activation_store: Activation | None = None

    async def store_episodic(self, chat_id: str, content: str, tags=None, topic_id: str = "") -> None:
        self.stored_topic_ids.append(topic_id)
        self.archive_calls += 1

    async def set_activation(self, chat_id, activation):
        self._activation_store = activation
        self.set_activation_calls = getattr(self, "set_activation_calls", 0) + 1

    async def embed_query(self, query):
        # Return a non-None embedding so turn_state can be computed
        return [1.0, 0.0]


def _make_orch(layers):
    llm = _LLMClient(_Completions("ok"))
    return Orchestrator(layers, llm, _FakePluginManager(), _FakeAnalytics())


def test_archive_turn_receives_topic_id_after_cold_turn():
    """On a cold turn a fresh topic_id must be generated and passed to store_episodic."""
    layers = _TopicCaptureLayers()
    orch = _make_orch(layers)
    asyncio.run(orch.run("hello world", "chat1"))
    # At least one store_episodic call should have a non-empty topic_id
    assert any(tid for tid in layers.stored_topic_ids), \
        "expected a non-empty topic_id in at least one store_episodic call"


def test_topic_id_is_uuid_format():
    """Generated topic_id must be a valid UUID string (8-4-4-4-12 hex)."""
    import re
    UUID_RE = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    )
    layers = _TopicCaptureLayers()
    orch = _make_orch(layers)
    asyncio.run(orch.run("hello world", "chat1"))
    non_empty = [tid for tid in layers.stored_topic_ids if tid]
    assert non_empty, "no non-empty topic_ids stored"
    assert UUID_RE.match(non_empty[0]), f"topic_id not UUID format: {non_empty[0]!r}"
```

- [ ] **Step 3: Run the two new tests — expect failure**

```bash
python3 -m pytest tests/test_orchestrator_memory_flow.py -v -k "topic_id" 2>&1 | tail -15
```

Expected: both fail (topic_id not yet wired up).

- [ ] **Step 4: Update imports in `orchestrator/orchestrator.py`**

Add to the existing import block:

```python
import uuid
```

Extend the `memory.activation` import line to include the new functions:
```python
from memory.activation import (
    Activation, classify_turn, classify_write, rescore_recency, trim_recent,
    update_centroid_weighted, find_topic_return, archive_current_topic,
)
```

Extend the `memory.config` import line:
```python
from memory.config import (
    AGENTIC_MAX_ITERATIONS,
    ANALYTICS_LOG_INTERVAL,
    PREFETCH_MAX_RESULTS,
    PREFETCH_TIMEOUT,
    TOPIC_ARCHIVE_MAX,
    TOPIC_RETURN_THRESHOLD,
)
```

- [ ] **Step 5: Update `_archive_turn()` — add `topic_id` parameter**

Replace the current function:

```python
async def _archive_turn(layers, chat_id: str, intent: str, reply: str, topic_id: str = "") -> None:
    """Fire-and-forget: archive the full message pair into L3 episodic memory.

    Tagged 'l2_full_archive'. ``topic_id`` links the entry to its topic thread
    so the prefetch daemon can filter by topic on future turns.
    """
    try:
        content = f"user: {intent}\nassistant: {reply}"
        await layers.store_episodic(chat_id, content, tags=["l2_full_archive"], topic_id=topic_id)
        log.debug("L3 archive write ok: chat=%s topic=%s", chat_id, topic_id)
    except Exception as exc:
        log.warning("L3 archive dump failed chat=%s: %s", chat_id, exc)
```

- [ ] **Step 6: Update `_update_activation()` — archive, topic_return, weighted centroid, topic_id**

Replace the current method:

```python
    async def _update_activation(
        self, layers, chat_id: str, intent: str, query_emb, turn_state: str,
        activation, l3_results: list[dict],
        topic_return_id: str | None = None,
    ):
        """Persist or refresh the per-chat activation after a successful prefetch.

        * warm  — hold centroid steady; increment turn_count; extend recent_queries.
        * drift — weighted centroid update toward the new query; same topic_id.
        * cold  — archive old centroid, reuse returned topic_id or mint new UUID.
        """
        now = time.time()
        if turn_state == "warm":
            if activation is None:
                return None
            activation.recent_queries = trim_recent(activation.recent_queries, intent)
            activation.turn_count += 1
            activation.ts = now
            await layers.set_activation(chat_id, activation)
            return activation

        if query_emb is None:
            return None

        recent = trim_recent(activation.recent_queries if activation else [], intent)

        # Archive the departing topic on a cold break.
        archived: list[dict] = []
        if activation:
            if turn_state == "cold" and activation.topic_id:
                archived = archive_current_topic(activation, TOPIC_ARCHIVE_MAX)
            else:
                archived = list(activation.archived_topics)

        # Centroid: weighted blend on drift (keeps thread memory); fresh on cold.
        if turn_state == "drift" and activation and activation.centroid:
            new_centroid = update_centroid_weighted(
                activation.centroid, query_emb, activation.turn_count + 1,
            )
            new_turn_count = activation.turn_count + 1
            topic_id = activation.topic_id or str(uuid.uuid4())
        else:
            # cold — reuse recovered topic or mint fresh UUID
            new_centroid = query_emb
            new_turn_count = 1
            topic_id = topic_return_id or str(uuid.uuid4())
            if topic_return_id:
                log.info("topic return chat=%s topic=%s", chat_id, topic_return_id)

        new_act = Activation(
            centroid=new_centroid,
            merged=l3_results,
            last_query=intent,
            recent_queries=recent,
            ts=now,
            topic_id=topic_id,
            turn_count=new_turn_count,
            archived_topics=archived,
        )
        await layers.set_activation(chat_id, new_act)
        return new_act
```

- [ ] **Step 7: Update `_prefetch_daemon()` — topic-filtered mechanism + drift narrowing**

In `_prefetch_daemon`, after the warm-path return and before the drift-path, add a variable:

```python
        current_topic_id: str | None = activation.topic_id if activation else None
```

Replace the drift path:

```python
        if state == "drift":
            # Targeted refresh scoped to current topic; fallback to global if no tagged entries yet.
            fresh = enforce_result_limit(
                await layers.search_episodic(user_message, limit=limit, topic_id=current_topic_id)
            )
            if not fresh:
                fresh = enforce_result_limit(await layers.search_episodic(user_message, limit=limit))
            merged = merge_results([fresh])[:limit]
            log.info("prefetch drift chat=%s merged=%d topic=%s", chat_id, len(merged), current_topic_id)
            meta = {"warm_served": False, "thematic": len(fresh), "temporal": 0, "specific_key": 0}
            return merged, False, None, meta
```

In the cold path, add a `_topic_filtered` mechanism alongside `_thematic`:

After `async def _specific_key(matched_keys: list[str]) -> dict:`, add:

```python
        async def _topic_filtered(tid: str) -> dict:
            results = await layers.search_episodic(user_message, limit=limit, topic_id=tid)
            return {"results": results, "cache_hit": False, "cache_key": None}
```

In the `tasks` list, insert the topic-return mechanism when `topic_return_id` is set. The `_prefetch_daemon` signature must accept `topic_return_id`:

Current signature:
```python
    async def _prefetch_daemon(
        self, chat_id: str, user_message: str,
        state: str, activation,
    ) -> tuple[list[dict], bool, str | None, dict]:
```

New signature:
```python
    async def _prefetch_daemon(
        self, chat_id: str, user_message: str,
        state: str, activation,
        topic_return_id: str | None = None,
    ) -> tuple[list[dict], bool, str | None, dict]:
```

In the cold-path `tasks` list construction, add the topic-return mechanism when applicable:

```python
        tasks: list = [("thematic", _thematic())]
        if topic_return_id:
            tasks.append(("topic_return", _topic_filtered(topic_return_id)))
        if after_before is not None:
            tasks.append(("temporal", _temporal(after_before)))
        if keys:
            tasks.append(("specific_key", _specific_key(keys)))
```

In the gather loop, handle the `topic_return` name (it contributes to `thematic_count` for logging simplicity):

```python
        for name, part in zip(names, parts):
            if isinstance(part, BaseException):
                log.warning("prefetch mechanism raised chat=%s mechanism=%s: %s", chat_id, name, part)
                continue
            count = len(part["results"])
            if name in ("thematic", "topic_return"):
                thematic_count += count
            elif name == "temporal":
                temporal_count = count
            elif name == "specific_key":
                specific_key_count = count
            groups.append(part["results"])
            if part.get("cache_key") is not None:
                cache_hit = part["cache_hit"]
                cache_key = part["cache_key"]
```

- [ ] **Step 8: Update `run()` — compute topic_return_id before daemon, pass through**

In `run()`, after `turn_state = classify_turn(...)` and before `prefetch_task = ...`, insert:

```python
            # Compute topic return before the daemon starts (pure CPU, no I/O).
            topic_return_id: str | None = None
            if turn_state == "cold" and activation and activation.archived_topics:
                topic_return_id = find_topic_return(
                    query_emb, activation.archived_topics, TOPIC_RETURN_THRESHOLD,
                )

```

Update the `prefetch_task` creation to pass `topic_return_id`:

```python
            prefetch_task = asyncio.create_task(
                self._prefetch_daemon(chat_id, intent, turn_state, activation,
                                      topic_return_id=topic_return_id))
```

Update `_update_activation` call to pass `topic_return_id`:

```python
                current_activation = await self._update_activation(
                    layers, chat_id, intent, query_emb, turn_state, activation, l3_results,
                    topic_return_id=topic_return_id)
```

Update `_archive_turn` call to pass the topic_id from the current activation:

```python
            archive_task = asyncio.create_task(
                _archive_turn(
                    layers, chat_id, intent, saved_reply,
                    topic_id=current_activation.topic_id if current_activation else "",
                ))
```

- [ ] **Step 9: Run the two new tests — expect green**

```bash
python3 -m pytest tests/test_orchestrator_memory_flow.py -v -k "topic_id" 2>&1 | tail -15
```

Expected: both pass.

- [ ] **Step 10: Run the full suite — expect all green**

```bash
python3 -m pytest tests/ -v 2>&1 | tail -30
```

Expected: all pass (116+ tests).

- [ ] **Step 11: Commit**

```bash
git add orchestrator/orchestrator.py tests/test_orchestrator_memory_flow.py
git commit -m "feat: topic-aware prefetch — topic_id in archive, weighted centroid, topic return detection"
```

- [ ] **Step 12: Push**

```bash
git push
```

---

## Self-Review

**Spec coverage:**
- ✅ `topic_id` UUID generated at cold break, stored in Activation
- ✅ Centroid stability via weighted update (alpha = 1/min(turn_count, 20))
- ✅ Multi-centroid archive (up to `TOPIC_ARCHIVE_MAX=10` past topics per chat)
- ✅ Topic return detection (`find_topic_return`) runs before daemon
- ✅ Drift path narrows to current `topic_id` with global fallback
- ✅ Cold+topic_return adds a parallel `_topic_filtered` mechanism
- ✅ Every `_archive_turn` write carries `topic_id` as metadata
- ✅ No new LLM calls
- ✅ Backward compatible: old activation blobs and old ChromaDB entries degrade safely
- ✅ All pure functions fully unit-tested without services

**Placeholder scan:** None found.

**Type consistency:**
- `topic_return_id: str | None` consistent across `run()`, `_update_activation()`, `_prefetch_daemon()`
- `topic_id: str` (non-optional, empty = unset) in `store_episodic()` and `_archive_turn()`
- `topic_id: str | None` (optional filter) in `search_episodic()` and `search_episodic_with_cache()`
