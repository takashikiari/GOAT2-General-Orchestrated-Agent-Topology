## Task 6 Report — Registry wires GLiNERExtractor + chat_id-scoped thematic prefetch

**Status:** COMPLETE

**Commit:** `8fa6509`

**Test summary:** 179 passed, 0 failed (3 new tests in `tests/test_chat_id_scoped_search.py`)

---

### Changes made

**`memory/episodic/episodic.py`**
- Added `chat_id_filter: str | None = None` to `search()` signature
- Appends `{"chat_id": {"$eq": chat_id_filter}}` to `clauses` when not None

**`memory/layers.py`**
- `search_episodic()`: added `chat_id_filter=None`, passed through to `self._episodic.search()`
- `search_episodic_with_cache()`: added `chat_id_filter=None`, passed to `_search_cache_key()` and `self._episodic.search()`
- `_search_cache_key()`: added `chat_id_filter=None` to signature; included in `key_str = query + (topic_id or "") + (chat_id_filter or "")` so scoped/global searches have distinct cache entries

**`orchestrator/orchestrator.py`**
- Removed `extract_structural_keys` from import (only `extract_temporal_range` kept)
- Removed `keys = extract_structural_keys(user_message)` line
- Removed `_specific_key()` coroutine definition
- Added `_thematic_scoped()` coroutine (always runs on cold path, passes `chat_id_filter=chat_id`)
- Updated `tasks` list: `[("thematic", _thematic()), ("thematic_scoped", _thematic_scoped())]`
- Updated mechanism counting: `if name in ("thematic", "topic_return", "thematic_scoped"):` counts as thematic
- Updated log message: removed `specific_key=%d` from format string
- Updated `meta` dict: `"specific_key": 0` hardcoded (observability backward-compat)
- Cleaned up debug log: removed `specific_key=%s keys=%s` fields

**`registry/registry.py`**
- Added `from memory.gliner_extractor import GLiNERExtractor` import (module-level)
- Added `self._gliner_extractor: GLiNERExtractor | None = None` in `__init__`
- Added `gliner_extractor` lazy property (returns `GLiNERExtractor()` on first call, stores in `self._gliner_extractor`)
- Updated `memory_layers` property to pass `extractor=self.gliner_extractor`

**`requirements.txt`**
- Added `#   gliner>=0.2.0      # memory/gliner_extractor.py (L3 entity enrichment; ~200MB model download on first use)` as commented optional dependency

**`tests/test_chat_id_scoped_search.py`** (new)
- `test_search_episodic_passes_chat_id_filter`: verifies `chat_id_filter` propagates to `episodic.search`
- `test_search_cache_key_differs_with_chat_id_filter`: verifies different `chat_id_filter` values produce distinct cache keys
- `test_episodic_search_chat_id_filter_adds_clause`: verifies `chat_id_filter` value appears in ChromaDB `where` clause

---

### Concerns

None. All constraints met:
- `_thematic_scoped` always runs on cold path (no conditional gate)
- `_specific_key` and `extract_structural_keys` fully removed (verified with grep)
- `specific_key: 0` kept in meta for observability compat
- `chat_id_filter` in `_search_cache_key` digest ensures scoped/global cache isolation
- `GLiNERExtractor` import at module level in registry.py
- Full suite: 179/179 passed
