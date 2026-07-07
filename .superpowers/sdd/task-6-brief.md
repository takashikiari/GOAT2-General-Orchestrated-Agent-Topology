### Task 6: Registry wires GLiNERExtractor + chat_id-scoped thematic prefetch

**Files:**
- Modify: `registry/registry.py` — add `gliner_extractor` lazy property; pass to `MemoryLayers`
- Modify: `memory/layers.py` — `search_episodic` and `search_episodic_with_cache` accept `chat_id_filter`; `_search_cache_key` includes it
- Modify: `memory/episodic/episodic.py` — `search()` accepts `chat_id_filter` parameter
- Modify: `orchestrator/orchestrator.py` — `_prefetch_daemon` adds `_thematic_scoped`; removes `_specific_key` and `extract_structural_keys` import
- Modify: `requirements.txt` — add `gliner` (optional, commented)
- Test: `tests/test_chat_id_scoped_search.py`

**Interfaces:**
- Consumes: `MemoryLayers.__init__(working, episodic, permanent, cache_ttl, extractor)` from Task 5
- Produces: `EpisodicMemory.search(..., chat_id_filter=None)` — adds `chat_id` clause to ChromaDB `where`
- Produces: `MemoryLayers.search_episodic(..., chat_id_filter=None)` and `search_episodic_with_cache(..., chat_id_filter=None)`
- Produces: `_thematic_scoped()` in `_prefetch_daemon` — always runs on cold path alongside global `_thematic()`

- [ ] **Step 1: Write the failing tests**

```python
"""tests.test_chat_id_scoped_search — chat_id_filter parameter on search/search_episodic_with_cache."""
from __future__ import annotations
import asyncio
from unittest.mock import MagicMock, AsyncMock


def _make_episodic_mock(return_docs=None):
    ep = MagicMock()
    ep._write_lock = MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock())
    ep.search = AsyncMock(return_value=return_docs or [])
    return ep


def test_search_episodic_passes_chat_id_filter():
    """layers.search_episodic passes chat_id_filter to episodic.search."""
    from memory.layers import MemoryLayers
    ep = _make_episodic_mock()
    layers = MemoryLayers(MagicMock(), ep, MagicMock())
    asyncio.run(layers.search_episodic("query", chat_id_filter="chat42"))
    call_kwargs = ep.search.call_args[1]
    assert call_kwargs.get("chat_id_filter") == "chat42"


def test_search_cache_key_differs_with_chat_id_filter():
    """Different chat_id_filter values produce different cache keys."""
    from memory.layers import MemoryLayers
    key_global = MemoryLayers._search_cache_key("query", topic_id=None, chat_id_filter=None)
    key_scoped = MemoryLayers._search_cache_key("query", topic_id=None, chat_id_filter="chat42")
    assert key_global != key_scoped


def test_episodic_search_chat_id_filter_adds_clause():
    """EpisodicMemory.search adds chat_id clause when chat_id_filter given."""
    from memory.episodic.episodic import EpisodicMemory
    ep = EpisodicMemory()
    mock_col = MagicMock()
    mock_col.query.return_value = {
        "documents": [[]], "metadatas": [[]], "distances": [[]]
    }
    ep._collection = mock_col
    asyncio.run(ep.search("query", chat_id_filter="chat-xyz"))
    call_kwargs = mock_col.query.call_args[1]
    where = call_kwargs.get("where") or {}
    # chat_id should be in where clause (either direct or in $and)
    where_str = str(where)
    assert "chat-xyz" in where_str
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/lenovo/workspace/goat2 && python -m pytest tests/test_chat_id_scoped_search.py -v 2>&1 | head -20
```
Expected: FAIL (chat_id_filter parameter doesn't exist yet).

- [ ] **Step 3: Modify `memory/episodic/episodic.py` — add chat_id_filter to search()**

In `search()` method, add `chat_id_filter: str | None = None` parameter and add a clause:

```python
    async def search(
        self, query: str, limit: int = 5,
        after: float | None = None, before: float | None = None,
        topic_id: str | None = None,
        chat_id_filter: str | None = None,
    ) -> list[dict]:
```

In the clauses list, add:
```python
        if chat_id_filter is not None:
            clauses.append({"chat_id": {"$eq": chat_id_filter}})
```
(add this alongside the existing `after`, `before`, `topic_id` clauses)

- [ ] **Step 4: Modify `memory/layers.py` — search_episodic, search_episodic_with_cache, _search_cache_key**

Update `search_episodic`:
```python
    async def search_episodic(
        self, query: str, limit: int = 5,
        after: float | None = None, before: float | None = None,
        topic_id: str | None = None,
        chat_id_filter: str | None = None,
    ) -> list[dict]:
        results = await self._episodic.search(
            query, limit=limit, after=after, before=before,
            topic_id=topic_id, chat_id_filter=chat_id_filter,
        )
        return enforce_result_limit(results)
```

Update `search_episodic_with_cache`:
```python
    async def search_episodic_with_cache(
        self, chat_id: str, query: str, limit: int = 5,
        topic_id: str | None = None,
        chat_id_filter: str | None = None,
    ) -> tuple[list[dict], bool, str]:
        cache_key = self._search_cache_key(query, topic_id, chat_id_filter)
        cached = await self._cache.get(chat_id, cache_key)
        if cached is not None:
            return cached["results"], True, cache_key
        log.debug("episodic search (cache miss) chat=%s query=%r", chat_id, query[:80])
        results = enforce_result_limit(
            await self._episodic.search(
                query, limit=limit, topic_id=topic_id, chat_id_filter=chat_id_filter,
            )
        )
        await self._cache.set(chat_id, cache_key, {"results": results})
        return results, False, cache_key
```

Update `_search_cache_key`:
```python
    @staticmethod
    def _search_cache_key(
        query: str, topic_id: str | None = None, chat_id_filter: str | None = None,
    ) -> str:
        key_str = query + (topic_id or "") + (chat_id_filter or "")
        digest = hashlib.sha256(key_str.encode("utf-8")).hexdigest()[:16]
        return f"{_SEARCH_NAMESPACE}:{digest}"
```

- [ ] **Step 5: Modify `orchestrator/orchestrator.py` — add _thematic_scoped, remove _specific_key**

In `_prefetch_daemon`, in the cold path section:

Remove the `_specific_key` coroutine definition (lines ~532-534) and `keys = extract_structural_keys(user_message)` (line ~498).

Add `_thematic_scoped` coroutine:
```python
        async def _thematic_scoped() -> dict:
            results, hit, key = await layers.search_episodic_with_cache(
                chat_id, user_message, limit=limit, chat_id_filter=chat_id,
            )
            return {"results": results, "cache_hit": hit, "cache_key": key}
```

Update the tasks list:
```python
        tasks: list = [("thematic", _thematic()), ("thematic_scoped", _thematic_scoped())]
        if topic_return_id:
            tasks.append(("topic_return", _topic_filtered(topic_return_id)))
        if after_before is not None:
            tasks.append(("temporal", _temporal(after_before)))
```

Update the mechanism counting loop — `thematic_scoped` counts toward thematic:
```python
            if name in ("thematic", "topic_return", "thematic_scoped"):
                thematic_count += count
```

Update the log line to remove `specific_key`:
```python
        log.info(
            "prefetch merge chat=%s state=cold mechanisms=%d merged=%d thematic=%d temporal=%d",
            chat_id, len(parts), len(merged), thematic_count, temporal_count,
        )
```

Update the meta dict:
```python
        meta = {
            "warm_served": False,
            "thematic": thematic_count,
            "temporal": temporal_count,
            "specific_key": 0,  # removed mechanism; kept key for observability compat
        }
```

Remove the import of `extract_structural_keys` from the top of orchestrator.py:
```python
from memory.query_classifier import extract_temporal_range
```
(remove `extract_structural_keys` from this import line)

- [ ] **Step 6: Update registry to add GLiNERExtractor + pass to MemoryLayers**

In `registry/registry.py`, add after existing imports:
```python
from memory.gliner_extractor import GLiNERExtractor
```

Add attribute to `__init__`:
```python
        self._gliner_extractor: GLiNERExtractor | None = None
```

Add property:
```python
    @property
    def gliner_extractor(self) -> GLiNERExtractor:
        """Shared GLiNERExtractor; model loads lazily on first extraction call."""
        if self._gliner_extractor is None:
            self._gliner_extractor = GLiNERExtractor()
        return self._gliner_extractor
```

Update `memory_layers` property:
```python
    @property
    def memory_layers(self) -> MemoryLayers:
        if self._memory_layers is None:
            self._memory_layers = MemoryLayers(
                self.working_memory, self.episodic_memory, self.permanent_memory,
                cache_ttl=SESSION_CACHE_TTL,
                extractor=self.gliner_extractor,
            )
        return self._memory_layers
```

- [ ] **Step 7: Update requirements.txt**

Add GLiNER as an optional dependency (commented, not required for core operation):
```
#   gliner>=0.2.0      # memory/gliner_extractor.py (L3 entity enrichment; ~200MB model download on first use)
```

- [ ] **Step 8: Run all tests**

```bash
cd /home/lenovo/workspace/goat2 && python -m pytest tests/ -v 2>&1 | tail -30
```
Expected: All tests PASS including new chat_id scoped search tests.

- [ ] **Step 9: Commit**

```bash
git add memory/episodic/episodic.py memory/layers.py orchestrator/orchestrator.py registry/registry.py requirements.txt tests/test_chat_id_scoped_search.py
git commit -m "feat: chat_id-scoped thematic search replaces regex specific-key; registry wires GLiNER"
```

---

## Self-Review

**Spec coverage:**
- GLiNER extractor with lazy loading ✓ (Task 1)
- `update_metadata` on EpisodicQueries ✓ (Task 2)
- `enrich_l3_entry` helper ✓ (Task 2)
- `store()` returns doc_id, accepts pre-generated doc_id ✓ (Task 3)
- `store_episodic()` returns doc_id, accepts doc_id ✓ (Task 3)
- `_archive_turn` accepts doc_id ✓ (Task 3)
- Orchestrator pre-generates doc_id, stores as `l3_id` in L2 messages ✓ (Task 4)
- auto_promote enriches dropped messages with `l3_id` ✓ (Task 5)
- `MemoryLayers.__init__` accepts extractor ✓ (Task 5)
- `search()` accepts `chat_id_filter` ✓ (Task 6)
- `search_episodic*` propagates `chat_id_filter` ✓ (Task 6)
- `_search_cache_key` includes `chat_id_filter` ✓ (Task 6)
- `_thematic_scoped` always runs on cold path ✓ (Task 6)
- `_specific_key` and `extract_structural_keys` removed ✓ (Task 6)
- Registry wires GLiNER ✓ (Task 6)
- `requirements.txt` updated ✓ (Task 6)
- No summaries, no LLM at write time ✓

**Placeholder scan:** None found — all steps contain actual code.

**Type consistency:**
- `GLiNERExtractor.extract()` returns `dict` with keys `entities`, `entity_types`, `memory_type` — used identically in enrichment.py ✓
- `update_metadata(doc_id: str, updates: dict) -> None` — matches calls in enrichment.py ✓
- `store(..., doc_id=None) -> str` — matches layers.py and orchestrator.py calls ✓
- `schedule_auto_promote(chat_id, working, episodic=None, extractor=None)` — backward-compatible call in layers.py ✓
- `_search_cache_key(query, topic_id, chat_id_filter)` — all call sites updated ✓
