# Real-Data Prefetch Benchmark — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the §4 components of `docs/superpowers/specs/2026-07-08-real-data-prefetch-benchmark-design.md` — a benchmark layer that evaluates the RRF prefetch pipeline on real, unedited episodic-memory content without touching the live ChromaDB collection.

**Architecture:** A read-only snapshot script copies the live collection into an isolated `chroma_data_benchmark/` path (now possible via the `EpisodicMemory(storage_path=...)` / `ServiceRegistry(episodic_storage_path=...)` override added in the prior review pass). A mining module generates (query, expected_fact) ground truth per information-dense entry via one LLM call each, cached to disk. A retrieval-only harness (`prefetch_bench.py`) calls `memory.retrieval.retrieve()` directly to measure hit@K/mean-rank/mechanism attribution (now possible via the labeled-groups `mechanisms` field added to `merge_results` in the prior review pass). A full-cycle harness (`conversation_runner.py`) drives real `Orchestrator.run()` turns to compare warm vs. cold LLM behavior, capturing the exact injected context via the `on_context_assembled` callback added in the prior review pass, and scores groundedness via a new `Evaluator.groundedness_judge`.

**Tech Stack:** Python 3.12, pytest + pytest-asyncio, ChromaDB (`chromadb.PersistentClient`), the existing `AsyncOpenAI`-compatible `registry.llm_client`.

## Global Constraints

- No contamination of live data: every new script/module must operate against `chroma_data_benchmark/` (or a client explicitly pointed there), never the live `EPISODIC_STORAGE_PATH` collection, except the snapshot script's read-only export step (spec §2).
- No artificial simplification of mined content — ground truth is generated from unedited real entries (spec §2).
- Ground truth generation reuses `registry.llm_client` — no separate offline judge model (spec §2).
- File-size discipline: new modules are single-responsibility and stay small; split rather than grow an existing large file further (spec §2, and this project's own file-size convention).
- `chroma_data_benchmark/` and `benchmark/data/` must never be committed — add both to `.gitignore` (spec §8).
- TDD throughout: a failing test before any implementation code, for every task below.

---

## File Structure

New files this plan creates:
- `scripts/snapshot_episodic_for_benchmark.py` — read-only live→benchmark ChromaDB export (spec §4.1).
- `benchmark/mining_candidates.py` — pure candidate-selection heuristic (part of spec §4.2).
- `benchmark/real_data_mining.py` — LLM ground-truth generation + disk cache (spec §4.2).
- `benchmark/prefetch_metrics.py` — hit@K / mean-rank aggregation dataclass (part of spec §4.3).
- `benchmark/prefetch_bench.py` — retrieval-only harness calling `memory.retrieval.retrieve()` (spec §4.3).
- `benchmark/conversation_runner.py` — warm/cold full-cycle turn logic (spec §4.4).

Existing files this plan modifies:
- `utils/llm_utils.py` — rename `_extract_json` → `extract_json` (make public; it currently has zero callers anywhere in the codebase, so this is a safe, isolated rename before its first two real consumers land in this plan).
- `benchmark/evaluator.py` — add `Evaluator.groundedness_judge` (spec §4.6).
- `benchmark/runner.py` — rename `_snapshot`→`snapshot_analytics`, `_diff`→`diff_analytics` (module-level helpers become a second module's dependency, so the leading underscore is no longer accurate); add a thin `run_conversation` delegator method (spec §4.4).
- `.gitignore` — add `chroma_data_benchmark/` and `benchmark/data/` (spec §8).

Test files this plan creates:
- `tests/test_snapshot_episodic_for_benchmark.py`
- `tests/test_mining_candidates.py`
- `tests/test_real_data_mining.py`
- `tests/test_prefetch_metrics.py`
- `tests/test_prefetch_bench.py`
- `tests/test_evaluator_groundedness.py`
- `tests/test_conversation_runner.py`

Out of scope for this plan (per spec §9, deferred): automatic snapshot refresh scheduling, cross-run trend dashboards, rewriting the 16 existing synthetic datasets.

---

### Task 1: Snapshot script — `scripts/snapshot_episodic_for_benchmark.py`

**Files:**
- Create: `scripts/snapshot_episodic_for_benchmark.py`
- Test: `tests/test_snapshot_episodic_for_benchmark.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: `memory.config.EPISODIC_COLLECTION_NAME`, `memory.config.EPISODIC_STORAGE_PATH` (existing constants).
- Produces: `export_snapshot(source_col, dest_client, collection_name: str) -> int` (row count written; raises `RuntimeError` on any row-count mismatch, before or after write) — this is the function later tasks' documentation references as "the snapshot script"; no other task calls it directly.

- [ ] **Step 1: Write the failing tests**

```python
"""tests.test_snapshot_episodic_for_benchmark — read-only live->benchmark export (spec §4.1).

Fakes both the source and destination ChromaDB collections/client so no real
ChromaDB is touched — mirrors the row-count safety check already proven out
in scripts/repair_episodic.py.
"""
from __future__ import annotations

import asyncio

from scripts.snapshot_episodic_for_benchmark import export_snapshot


class _FakeSourceCollection:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def get(self, include=None):
        return {
            "ids": [r["id"] for r in self._rows],
            "documents": [r["content"] for r in self._rows],
            "metadatas": [r["metadata"] for r in self._rows],
        }

    def count(self) -> int:
        return len(self._rows)


class _FakeDestCollection:
    def __init__(self) -> None:
        self.added = {"ids": [], "documents": [], "metadatas": []}

    def add(self, ids, documents, metadatas):
        self.added["ids"].extend(ids)
        self.added["documents"].extend(documents)
        self.added["metadatas"].extend(metadatas)

    def count(self) -> int:
        return len(self.added["ids"])


class _FakeDestClient:
    def __init__(self) -> None:
        self.collections: dict[str, _FakeDestCollection] = {}
        self.deleted: list[str] = []

    def delete_collection(self, name):
        self.deleted.append(name)
        self.collections.pop(name, None)

    def get_or_create_collection(self, name):
        if name not in self.collections:
            self.collections[name] = _FakeDestCollection()
        return self.collections[name]


def test_export_snapshot_copies_rows_verbatim():
    rows = [
        {"id": "a", "content": "hello", "metadata": {"chat_id": "c1"}},
        {"id": "b", "content": "world", "metadata": {"chat_id": "c2"}},
    ]
    source = _FakeSourceCollection(rows)
    dest_client = _FakeDestClient()
    count = asyncio.run(export_snapshot(source, dest_client, "bench_col"))
    assert count == 2
    dest_col = dest_client.collections["bench_col"]
    assert dest_col.added["ids"] == ["a", "b"]
    assert dest_col.added["documents"] == ["hello", "world"]
    assert dest_col.added["metadatas"] == [{"chat_id": "c1"}, {"chat_id": "c2"}]


def test_export_snapshot_aborts_on_row_count_mismatch():
    rows = [{"id": "a", "content": "x", "metadata": {}}]
    source = _FakeSourceCollection(rows)
    source.count = lambda: 5  # simulate a desync between get() and count()
    dest_client = _FakeDestClient()
    raised = False
    try:
        asyncio.run(export_snapshot(source, dest_client, "bench_col"))
    except RuntimeError as exc:
        raised = True
        assert "aborting" in str(exc)
    assert raised, "expected RuntimeError on row-count mismatch"
    assert "bench_col" not in dest_client.collections  # no write happened


def test_export_snapshot_is_idempotent_drop_and_recreate():
    rows = [{"id": "a", "content": "x", "metadata": {}}]
    source = _FakeSourceCollection(rows)
    dest_client = _FakeDestClient()
    asyncio.run(export_snapshot(source, dest_client, "bench_col"))
    asyncio.run(export_snapshot(source, dest_client, "bench_col"))
    assert dest_client.deleted == ["bench_col", "bench_col"]
    assert dest_client.collections["bench_col"].count() == 1  # not doubled
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_snapshot_episodic_for_benchmark.py -v --tb=short -p no:logging`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.snapshot_episodic_for_benchmark'`

- [ ] **Step 3: Write the implementation**

```python
"""scripts.snapshot_episodic_for_benchmark — read-only live->benchmark ChromaDB export (spec §4.1).

Exports every (id, document, metadata) row from the live episodic collection
into a fresh, physically separate ChromaDB PersistentClient at a benchmark
path. Never opens the live collection for writing. Idempotent: re-running
drops and recreates the destination collection and re-exports from scratch,
so it is safe to refresh the snapshot at any time.
"""
from __future__ import annotations

import asyncio

__all__ = ["export_snapshot"]

_BATCH = 1000


async def export_snapshot(source_col, dest_client, collection_name: str) -> int:
    """Copy every row from ``source_col`` into a fresh collection on ``dest_client``.

    Returns the row count written. Raises ``RuntimeError`` (no write performed)
    if the exported row count doesn't match ``source_col.count()`` at export
    time — mirrors ``scripts/repair_episodic.py``'s existing safety check.
    Also raises if the post-write destination count doesn't match what was
    written (defensive: a partial/failed ``add`` should never look successful).
    """
    def _export():
        r = source_col.get(include=["documents", "metadatas"])
        ids = list(r.get("ids") or [])
        docs = list(r.get("documents") or [])
        metas = [dict(m or {}) for m in (r.get("metadatas") or [])]
        return ids, docs, metas

    ids, docs, metas = await asyncio.to_thread(_export)
    if len(ids) != source_col.count():
        raise RuntimeError(
            f"export rows ({len(ids)}) != source count ({source_col.count()}); aborting"
        )

    def _write():
        try:
            dest_client.delete_collection(collection_name)
        except Exception:  # noqa: BLE001 — collection may not exist yet
            pass
        dest_col = dest_client.get_or_create_collection(collection_name)
        for i in range(0, len(ids), _BATCH):
            dest_col.add(
                ids=ids[i:i + _BATCH],
                documents=docs[i:i + _BATCH],
                metadatas=metas[i:i + _BATCH],
            )
        return dest_col

    dest_col = await asyncio.to_thread(_write)
    if dest_col.count() != len(ids):
        raise RuntimeError(
            f"post-write count mismatch: wrote {len(ids)}, dest has {dest_col.count()}"
        )
    return len(ids)


async def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="Snapshot the live episodic ChromaDB into an isolated benchmark collection."
    )
    ap.add_argument(
        "--dest-path", default="chroma_data_benchmark",
        help="destination ChromaDB PersistentClient path (default: chroma_data_benchmark)",
    )
    args = ap.parse_args()

    import chromadb
    import posthog as _posthog
    from chromadb.config import Settings
    _posthog.disabled = True
    _posthog.capture = lambda *a, **k: None  # type: ignore[assignment]

    from memory.config import EPISODIC_COLLECTION_NAME, EPISODIC_STORAGE_PATH

    source_client = chromadb.PersistentClient(
        path=EPISODIC_STORAGE_PATH, settings=Settings(anonymized_telemetry=False),
    )
    source_col = source_client.get_or_create_collection(EPISODIC_COLLECTION_NAME)
    dest_client = chromadb.PersistentClient(
        path=args.dest_path, settings=Settings(anonymized_telemetry=False),
    )

    print(f"source: {EPISODIC_COLLECTION_NAME!r} at {EPISODIC_STORAGE_PATH} ({source_col.count()} rows)")
    count = await export_snapshot(source_col, dest_client, EPISODIC_COLLECTION_NAME)
    print(f"snapshot written: {count} rows -> {args.dest_path}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(asyncio.run(main()))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_snapshot_episodic_for_benchmark.py -v --tb=short -p no:logging`
Expected: 3 passed

- [ ] **Step 5: Add the gitignore entries (spec §8)**

Add to `.gitignore`, near the existing `chroma_data/` lines:

```
chroma_data_benchmark/
benchmark/data/
```

- [ ] **Step 6: Commit**

```bash
git add scripts/snapshot_episodic_for_benchmark.py tests/test_snapshot_episodic_for_benchmark.py .gitignore
git commit -m "feat(benchmark): add read-only live->benchmark ChromaDB snapshot script"
```

---

### Task 2: Candidate selection — `benchmark/mining_candidates.py`

**Files:**
- Create: `benchmark/mining_candidates.py`
- Test: `tests/test_mining_candidates.py`

**Interfaces:**
- Consumes: nothing (pure function over plain dicts shaped like `export_snapshot`'s source rows: `{"id": str, "content": str, "metadata": dict}`).
- Produces: `select_candidates(entries: list[dict], min_words: int = 15, min_importance: float = 0.3) -> list[dict]` — Task 3 (`real_data_mining.py`) imports and calls this.

- [ ] **Step 1: Write the failing tests**

```python
"""tests.test_mining_candidates — information-dense entry selection (spec §4.2)."""
from __future__ import annotations

from benchmark.mining_candidates import select_candidates


def _entry(content: str, importance: float | None = None) -> dict:
    metadata: dict = {}
    if importance is not None:
        metadata["importance"] = importance
    return {"id": "x", "content": content, "metadata": metadata}


def test_excludes_short_chit_chat():
    entries = [_entry("GOAT"), _entry("Ce faci nebunule?")]
    assert select_candidates(entries) == []


def test_includes_long_content_with_no_importance_metadata():
    long_text = " ".join(["word"] * 20)
    entries = [_entry(long_text)]
    assert select_candidates(entries) == entries


def test_excludes_long_content_with_low_importance():
    long_text = " ".join(["word"] * 20)
    entries = [_entry(long_text, importance=0.1)]
    assert select_candidates(entries) == []


def test_includes_long_content_with_high_importance():
    long_text = " ".join(["word"] * 20)
    entries = [_entry(long_text, importance=0.8)]
    assert select_candidates(entries) == entries
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_mining_candidates.py -v --tb=short -p no:logging`
Expected: FAIL with `ModuleNotFoundError: No module named 'benchmark.mining_candidates'`

- [ ] **Step 3: Write the implementation**

```python
"""benchmark.mining_candidates — information-dense entry selection for real-data mining (spec §4.2).

Pure function: filters exported snapshot entries down to candidates worth
generating a recall question for. Excludes short generic chit-chat; prefers
entries the enrichment pipeline (memory.enrichment.compute_importance) already
scored as important, when that metadata is present (older entries predate
enrichment and are judged on word count alone).
"""
from __future__ import annotations

__all__ = ["select_candidates"]

_MIN_WORDS = 15
_MIN_IMPORTANCE = 0.3


def select_candidates(
    entries: list[dict], min_words: int = _MIN_WORDS, min_importance: float = _MIN_IMPORTANCE,
) -> list[dict]:
    """Filter exported entries to information-dense candidates for ground-truth mining."""
    candidates = []
    for entry in entries:
        content = (entry.get("content") or "").strip()
        if len(content.split()) < min_words:
            continue
        importance = (entry.get("metadata") or {}).get("importance")
        if importance is not None and float(importance) < min_importance:
            continue
        candidates.append(entry)
    return candidates
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_mining_candidates.py -v --tb=short -p no:logging`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add benchmark/mining_candidates.py tests/test_mining_candidates.py
git commit -m "feat(benchmark): add pure candidate-selection heuristic for real-data mining"
```

---

### Task 3: Ground-truth generation — `benchmark/real_data_mining.py`

**Files:**
- Modify: `utils/llm_utils.py:96` (rename `_extract_json` → `extract_json`)
- Create: `benchmark/real_data_mining.py`
- Test: `tests/test_real_data_mining.py`

**Interfaces:**
- Consumes: `benchmark.mining_candidates.select_candidates` (Task 2); `utils.llm_utils.extract_json(text: str) -> dict` (renamed this task); an LLM client shaped like `registry.llm_client` (`AsyncOpenAI`-compatible, `.chat.completions.create(...)`); `config.settings.MODEL_NAME`.
- Produces: `generate_case(entry: dict, llm_client) -> dict | None`; `mine_cases(entries: list[dict], llm_client) -> list[dict]`; `load_or_mine(entries: list[dict], llm_client, cache_path: Path, force: bool = False) -> list[dict]`. Mined-case shape (used by Tasks 5 and 7): `{"id": str, "message_id": str, "chat_id_source": str, "lead_in_turns": list[str], "query": str, "expected_fact": str}`.

- [ ] **Step 1: Rename `_extract_json` to `extract_json`**

In `utils/llm_utils.py`, this function currently has zero callers anywhere in the codebase (verified via `grep -rn "_extract_json"`), so the rename is isolated — no other call site to update. Replace the existing function definition with:

```python
def extract_json(text: str) -> dict:
    """Extract a JSON object from raw LLM output.

    Handles: bare JSON, markdown fences (```json ... ```), and JSON embedded
    in prose. Raises ``ValueError`` if no valid JSON is found.
    """
    stripped = text.strip()

    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1))
        except json.JSONDecodeError:
            pass

    brace = re.search(r"\{.*\}", stripped, re.DOTALL)
    if brace:
        try:
            return json.loads(brace.group(0))
        except json.JSONDecodeError:
            pass

    raise ValueError(f"No valid JSON found in: {text[:200]!r}")
```

- [ ] **Step 2: Write the failing tests**

```python
"""tests.test_real_data_mining — LLM ground-truth generation + caching (spec §4.2)."""
from __future__ import annotations

import asyncio
import json

from benchmark.real_data_mining import generate_case, load_or_mine, mine_cases
from tests._orch_fakes import _Completions, _LLMClient


def _entry(content: str, message_id="msg1", chat_id="chat1") -> dict:
    return {
        "id": "row1", "content": content,
        "metadata": {"message_id": message_id, "chat_id": chat_id, "importance": 0.9},
    }


def test_generate_case_parses_llm_json_response():
    content = " ".join(["word"] * 20)
    reply = json.dumps({"query": "What time is the appointment?", "expected_fact": "9am"})
    llm = _LLMClient(_Completions(reply))
    case = asyncio.run(generate_case(_entry(content), llm))
    assert case["query"] == "What time is the appointment?"
    assert case["expected_fact"] == "9am"
    assert case["message_id"] == "msg1"
    assert case["chat_id_source"] == "chat1"
    assert case["lead_in_turns"] == [content]


def test_generate_case_returns_none_on_malformed_json():
    llm = _LLMClient(_Completions("not json at all"))
    case = asyncio.run(generate_case(_entry("x" * 100), llm))
    assert case is None


def test_generate_case_returns_none_on_empty_fields():
    reply = json.dumps({"query": "", "expected_fact": ""})
    llm = _LLMClient(_Completions(reply))
    case = asyncio.run(generate_case(_entry("x" * 100), llm))
    assert case is None


def test_mine_cases_skips_short_entries_and_failed_generations():
    long_content = " ".join(["word"] * 20)
    good_reply = json.dumps({"query": "q", "expected_fact": "f"})
    entries = [_entry("hi"), _entry(long_content)]  # first too short to be a candidate
    llm = _LLMClient(_Completions(good_reply))
    cases = asyncio.run(mine_cases(entries, llm))
    assert len(cases) == 1
    assert cases[0]["query"] == "q"


def test_load_or_mine_caches_to_disk(tmp_path):
    long_content = " ".join(["word"] * 20)
    good_reply = json.dumps({"query": "q", "expected_fact": "f"})
    llm = _LLMClient(_Completions(good_reply))
    cache_path = tmp_path / "real_recall_cases.json"

    cases = asyncio.run(load_or_mine([_entry(long_content)], llm, cache_path))
    assert len(cases) == 1
    assert cache_path.exists()

    # Second call must not re-mine — swap in a client that would error if called.
    class _ExplodingCompletions:
        async def create(self, **kw):
            raise AssertionError("mining ran again; cache was not used")

    class _ExplodingChat:
        completions = _ExplodingCompletions()

    class _ExplodingClient:
        chat = _ExplodingChat()

    cached = asyncio.run(load_or_mine([_entry(long_content)], _ExplodingClient(), cache_path))
    assert cached == cases
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_real_data_mining.py -v --tb=short -p no:logging`
Expected: FAIL with `ModuleNotFoundError: No module named 'benchmark.real_data_mining'`

- [ ] **Step 4: Write the implementation**

```python
"""benchmark.real_data_mining — LLM-generated recall ground truth from real snapshot content (spec §4.2).

One registry.llm_client call per candidate asks for a natural follow-up recall
question and the short exact fact an answer must contain. Results cache to
disk (benchmark/data/real_recall_cases.json by convention, gitignored) so
mining runs once, not on every benchmark invocation.
"""
from __future__ import annotations

import json
from pathlib import Path

from benchmark.mining_candidates import select_candidates
from utils.llm_utils import extract_json
from utils.logging.setup import get_logger

log = get_logger(__name__)

__all__ = ["generate_case", "mine_cases", "load_or_mine"]

_SYSTEM_PROMPT = (
    "You are generating a benchmark case from a real stored memory. Given the "
    "CONTENT below, produce a natural follow-up question a user might ask "
    "later to recall this information, and the short exact fact string an "
    "answer must contain to be correct. Reply with ONLY a JSON object: "
    '{"query": "...", "expected_fact": "..."}.'
)


async def generate_case(entry: dict, llm_client) -> dict | None:
    """One LLM call generating (query, expected_fact) for a mined candidate.

    Returns ``None`` (logged) on any call/parse/empty-field failure — the
    caller skips this candidate rather than aborting the batch (spec §6).
    """
    from config import settings
    content = entry.get("content", "")
    try:
        r = await llm_client.chat.completions.create(
            model=settings.MODEL_NAME,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"CONTENT:\n{content}"},
            ],
            temperature=0.4, max_tokens=200,
        )
        parsed = extract_json(r.choices[0].message.content or "")
        query = str(parsed["query"]).strip()
        expected_fact = str(parsed["expected_fact"]).strip()
        if not query or not expected_fact:
            raise ValueError("empty query or expected_fact")
    except Exception as exc:  # noqa: BLE001 — one bad candidate must not abort mining
        log.warning("generate_case failed id=%s: %s", entry.get("id"), exc)
        return None
    metadata = entry.get("metadata") or {}
    return {
        "id": entry["id"],
        "message_id": metadata.get("message_id") or entry["id"],
        "chat_id_source": metadata.get("chat_id", ""),
        "lead_in_turns": [content],
        "query": query,
        "expected_fact": expected_fact,
    }


async def mine_cases(entries: list[dict], llm_client) -> list[dict]:
    """Select candidates from ``entries`` and generate a case for each that succeeds."""
    cases = []
    for entry in select_candidates(entries):
        case = await generate_case(entry, llm_client)
        if case is not None:
            cases.append(case)
    return cases


async def load_or_mine(
    entries: list[dict], llm_client, cache_path: Path, force: bool = False,
) -> list[dict]:
    """Return cached mined cases from ``cache_path``, mining fresh only when needed."""
    if cache_path.exists() and not force:
        return json.loads(cache_path.read_text())
    cases = await mine_cases(entries, llm_client)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cases, indent=2))
    return cases
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_real_data_mining.py -v --tb=short -p no:logging`
Expected: 5 passed

- [ ] **Step 6: Run the full suite to confirm the `extract_json` rename broke nothing**

Run: `python3 -m pytest tests/ -q -p no:logging`
Expected: all passing, same count as before plus 9 (4 from Task 2 + 5 from this task)

- [ ] **Step 7: Commit**

```bash
git add utils/llm_utils.py benchmark/real_data_mining.py tests/test_real_data_mining.py
git commit -m "feat(benchmark): add LLM ground-truth mining with disk caching"
```

---

### Task 4: Aggregation — `benchmark/prefetch_metrics.py`

**Files:**
- Create: `benchmark/prefetch_metrics.py`
- Test: `tests/test_prefetch_metrics.py`

**Interfaces:**
- Consumes: nothing (pure dataclass over plain dicts).
- Produces: `PrefetchMetrics` dataclass with `total_cases: int`, `hit_rate_by_state: dict[str, float]`, `mean_rank_by_state: dict[str, float | None]`, `mechanism_hit_counts_by_state: dict[str, dict[str, int]]`, built via `PrefetchMetrics.from_results(results: list[dict]) -> PrefetchMetrics`. Expected input shape (produced by Task 5's `evaluate_case`): `{"case_id": ..., "states": {"cold"|"warm"|"drift": {"hit": bool, "rank": int|None, "mechanisms": list[str]}}}`.

- [ ] **Step 1: Write the failing tests**

```python
"""tests.test_prefetch_metrics — hit@K / mean-rank aggregation (spec §4.3)."""
from __future__ import annotations

from benchmark.prefetch_metrics import PrefetchMetrics


def test_from_results_computes_hit_rate_and_mean_rank_per_state():
    results = [
        {"case_id": "1", "states": {
            "cold": {"hit": True, "rank": 1, "mechanisms": ["bm25"]},
            "warm": {"hit": True, "rank": 1, "mechanisms": ["prediction"]},
            "drift": {"hit": False, "rank": None, "mechanisms": []},
        }},
        {"case_id": "2", "states": {
            "cold": {"hit": False, "rank": None, "mechanisms": []},
            "warm": {"hit": True, "rank": 2, "mechanisms": ["semantic_global", "bm25"]},
            "drift": {"hit": True, "rank": 1, "mechanisms": ["temporal"]},
        }},
    ]
    m = PrefetchMetrics.from_results(results)
    assert m.total_cases == 2
    assert m.hit_rate_by_state["cold"] == 0.5
    assert m.hit_rate_by_state["warm"] == 1.0
    assert m.hit_rate_by_state["drift"] == 0.5
    assert m.mean_rank_by_state["cold"] == 1.0
    assert m.mean_rank_by_state["warm"] == 1.5
    assert m.mean_rank_by_state["drift"] == 1.0
    assert m.mechanism_hit_counts_by_state["warm"] == {
        "prediction": 1, "semantic_global": 1, "bm25": 1,
    }


def test_from_results_handles_empty_list():
    m = PrefetchMetrics.from_results([])
    assert m.total_cases == 0
    assert m.hit_rate_by_state["cold"] == 0.0
    assert m.mean_rank_by_state["cold"] is None
    assert m.mechanism_hit_counts_by_state["cold"] == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_prefetch_metrics.py -v --tb=short -p no:logging`
Expected: FAIL with `ModuleNotFoundError: No module named 'benchmark.prefetch_metrics'`

- [ ] **Step 3: Write the implementation**

```python
"""benchmark.prefetch_metrics — hit@K / mean-rank aggregation for the retrieval-only benchmark (spec §4.3).

Mirrors benchmark.metrics.BenchmarkMetrics's from_results shape, scoped to
per-state retrieval quality plus a per-state breakdown of which mechanism(s)
contributed to each hit (mechanism attribution only exists for actual hits —
a miss has no mechanism to attribute).
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = ["PrefetchMetrics"]

_STATES = ("cold", "warm", "drift")


@dataclass
class PrefetchMetrics:
    """Aggregate hit@K, mean rank, and mechanism-hit counts, per turn state."""

    total_cases: int
    hit_rate_by_state: dict[str, float]
    mean_rank_by_state: dict[str, float | None]
    mechanism_hit_counts_by_state: dict[str, dict[str, int]]

    @classmethod
    def from_results(cls, results: list[dict]) -> "PrefetchMetrics":
        """Build aggregates from per-case results (see prefetch_bench.evaluate_case)."""
        hit_rate_by_state: dict[str, float] = {}
        mean_rank_by_state: dict[str, float | None] = {}
        mechanism_hit_counts_by_state: dict[str, dict[str, int]] = {}

        for state in _STATES:
            entries = [r["states"][state] for r in results if state in r.get("states", {})]
            hits = [e["hit"] for e in entries]
            ranks = [e["rank"] for e in entries if e["rank"] is not None]
            hit_rate_by_state[state] = (sum(hits) / len(hits)) if hits else 0.0
            mean_rank_by_state[state] = (sum(ranks) / len(ranks)) if ranks else None
            mech_counts: dict[str, int] = {}
            for e in entries:
                if not e["hit"]:
                    continue
                for mech in e.get("mechanisms", []):
                    mech_counts[mech] = mech_counts.get(mech, 0) + 1
            mechanism_hit_counts_by_state[state] = mech_counts

        return cls(
            total_cases=len(results),
            hit_rate_by_state=hit_rate_by_state,
            mean_rank_by_state=mean_rank_by_state,
            mechanism_hit_counts_by_state=mechanism_hit_counts_by_state,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_prefetch_metrics.py -v --tb=short -p no:logging`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add benchmark/prefetch_metrics.py tests/test_prefetch_metrics.py
git commit -m "feat(benchmark): add hit@K/mean-rank/mechanism aggregation for prefetch_bench"
```

---

### Task 5: Retrieval-only harness — `benchmark/prefetch_bench.py`

**Files:**
- Create: `benchmark/prefetch_bench.py`
- Test: `tests/test_prefetch_bench.py`

**Interfaces:**
- Consumes: `memory.retrieval.retrieve(layers, chat_id, query, state, activation, topic_return_id=None) -> tuple[list[dict], bool, str|None, dict]` (existing; each result dict now carries `mechanisms: list[str]` per the prior review pass); `memory.activation.Activation` dataclass (existing, all-default constructible: `Activation(merged=..., topic_id=...)`); `benchmark.prefetch_metrics.PrefetchMetrics` (Task 4). Mined-case shape from Task 3: needs `case["query"]`, `case["message_id"]`, `case.get("chat_id_source")`.
- Produces: `evaluate_case(layers, case: dict) -> dict` (`{"case_id", "states": {state: {"hit", "rank", "mechanisms"}}}`); `run_prefetch_benchmark(cases: list[dict], layers) -> PrefetchMetrics`.

- [ ] **Step 1: Write the failing tests**

```python
"""tests.test_prefetch_bench — retrieval-only RRF pipeline benchmark (spec §4.3, §7)."""
from __future__ import annotations

import asyncio

from benchmark.prefetch_bench import evaluate_case, run_prefetch_benchmark


def _hit(message_id: str) -> dict:
    return {"content": message_id, "metadata": {"message_id": message_id}}


class _FakeLayers:
    """cold state finds the target via bm25 only; warm/drift reuse cold's activation."""

    async def search_episodic_with_cache(self, chat_id, query, limit=5, chat_id_filter=None):
        return [], False, "key"

    async def search_episodic(self, query, limit=5, topic_id=None, **kw):
        return []

    async def bm25_search(self, query, limit=15):
        return [_hit("target")]

    async def extract_query_entities(self, query):
        return {"entities": [], "entity_types": []}

    async def boost_by_entities(self, query, results, pre_extracted=None):
        return results

    async def rerank(self, query, results):
        return results

    async def bump_access(self, chat_id, ids):
        pass


def test_evaluate_case_finds_hit_via_bm25_in_cold_state():
    layers = _FakeLayers()
    case = {"id": "c1", "query": "q", "message_id": "target", "chat_id_source": "src"}
    result = asyncio.run(evaluate_case(layers, case))
    assert result["states"]["cold"] == {"hit": True, "rank": 1, "mechanisms": ["bm25"]}


def test_evaluate_case_warm_state_reuses_cold_activation():
    layers = _FakeLayers()
    case = {"id": "c1", "query": "q", "message_id": "target", "chat_id_source": "src"}
    result = asyncio.run(evaluate_case(layers, case))
    assert result["states"]["warm"]["hit"] is True


def test_evaluate_case_reports_miss_when_id_not_found():
    layers = _FakeLayers()
    case = {"id": "c2", "query": "q", "message_id": "missing", "chat_id_source": "src"}
    result = asyncio.run(evaluate_case(layers, case))
    assert result["states"]["cold"] == {"hit": False, "rank": None, "mechanisms": []}


def test_run_prefetch_benchmark_aggregates_multiple_cases():
    layers = _FakeLayers()
    cases = [
        {"id": "c1", "query": "q1", "message_id": "target", "chat_id_source": "src"},
        {"id": "c2", "query": "q2", "message_id": "missing", "chat_id_source": "src"},
    ]
    metrics = asyncio.run(run_prefetch_benchmark(cases, layers))
    assert metrics.total_cases == 2
    assert metrics.hit_rate_by_state["cold"] == 0.5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_prefetch_bench.py -v --tb=short -p no:logging`
Expected: FAIL with `ModuleNotFoundError: No module named 'benchmark.prefetch_bench'`

- [ ] **Step 3: Write the implementation**

```python
"""benchmark.prefetch_bench — retrieval-only RRF pipeline benchmark (spec §4.3).

No LLM call: runs memory.retrieval.retrieve() directly in cold/warm/drift
states against a mined case's query, checking whether the ground-truth
message_id was retrieved, at what rank, and via which mechanism(s) — the
mechanisms field is carried by merge_results (memory/result_merger.py) as of
the prior review pass, so no extra plumbing is needed here.
"""
from __future__ import annotations

from benchmark.prefetch_metrics import PrefetchMetrics
from memory.activation import Activation
from memory.retrieval import retrieve

__all__ = ["evaluate_case", "run_prefetch_benchmark"]


def _score_hit(merged: list[dict], expected_message_id: str) -> dict:
    """Rank (1-indexed) + mechanisms of the ground-truth entry within ``merged``, if present."""
    for rank, r in enumerate(merged, start=1):
        if r.get("metadata", {}).get("message_id") == expected_message_id:
            return {"hit": True, "rank": rank, "mechanisms": r.get("mechanisms", [])}
    return {"hit": False, "rank": None, "mechanisms": []}


async def evaluate_case(layers, case: dict) -> dict:
    """Run one mined case through cold/warm/drift retrieve() and score each state.

    warm/drift reuse the SAME activation, built from the case's own cold-state
    result — this exercises the RRF/rescoring mechanics directly (spec goal 1),
    not a full multi-turn simulation (that is conversation_runner.run_conversation).
    """
    chat_id = case.get("chat_id_source") or "prefetch-bench"
    query = case["query"]
    expected_id = case["message_id"]

    cold_merged, *_ = await retrieve(layers, chat_id, query, "cold", None)
    activation = Activation(merged=cold_merged, topic_id="prefetch-bench-topic")

    warm_merged, *_ = await retrieve(layers, chat_id, query, "warm", activation)
    drift_merged, *_ = await retrieve(layers, chat_id, query, "drift", activation)

    return {
        "case_id": case.get("id"),
        "states": {
            "cold": _score_hit(cold_merged, expected_id),
            "warm": _score_hit(warm_merged, expected_id),
            "drift": _score_hit(drift_merged, expected_id),
        },
    }


async def run_prefetch_benchmark(cases: list[dict], layers) -> PrefetchMetrics:
    """Evaluate every case and aggregate into a PrefetchMetrics report."""
    results = [await evaluate_case(layers, case) for case in cases]
    return PrefetchMetrics.from_results(results)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_prefetch_bench.py -v --tb=short -p no:logging`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add benchmark/prefetch_bench.py tests/test_prefetch_bench.py
git commit -m "feat(benchmark): add retrieval-only prefetch_bench harness"
```

---

### Task 6: Groundedness judge — `Evaluator.groundedness_judge`

**Files:**
- Modify: `benchmark/evaluator.py` (add one static method, following the existing `llm_judge` pattern at the end of the class)
- Test: `tests/test_evaluator_groundedness.py`

**Interfaces:**
- Consumes: `utils.llm_utils.extract_json` (Task 3's renamed function); `config.settings.MODEL_NAME`; an LLM client shaped like `registry.llm_client`.
- Produces: `Evaluator.groundedness_judge(response: str, retrieved_context: str, llm_client=None) -> dict` returning `{"grounded": bool | None, "hallucinated_claims": list[str], "answered_without_evidence": bool}` — `grounded=None` means "no judge could run" (spec §6), not "not grounded". Task 7 (`conversation_runner.py`) calls this.

- [ ] **Step 1: Write the failing tests**

```python
"""tests.test_evaluator_groundedness — Evaluator.groundedness_judge (spec §4.6)."""
from __future__ import annotations

import asyncio
import json

from benchmark.evaluator import Evaluator
from tests._orch_fakes import _Completions, _LLMClient


def test_groundedness_judge_parses_grounded_response():
    reply = json.dumps({
        "grounded": True, "hallucinated_claims": [], "answered_without_evidence": False,
    })
    llm = _LLMClient(_Completions(reply))
    verdict = asyncio.run(Evaluator.groundedness_judge("The meeting is at 9am.", "context: 9am meeting", llm))
    assert verdict == {"grounded": True, "hallucinated_claims": [], "answered_without_evidence": False}


def test_groundedness_judge_reports_hallucinated_claims():
    reply = json.dumps({
        "grounded": False,
        "hallucinated_claims": ["the meeting is in Paris"],
        "answered_without_evidence": False,
    })
    llm = _LLMClient(_Completions(reply))
    verdict = asyncio.run(Evaluator.groundedness_judge("The meeting is in Paris.", "context: 9am meeting", llm))
    assert verdict["grounded"] is False
    assert verdict["hallucinated_claims"] == ["the meeting is in Paris"]


def test_groundedness_judge_degrades_to_none_with_no_llm_client():
    verdict = asyncio.run(Evaluator.groundedness_judge("anything", "context", None))
    assert verdict == {"grounded": None, "hallucinated_claims": [], "answered_without_evidence": False}


def test_groundedness_judge_degrades_to_none_on_malformed_json():
    llm = _LLMClient(_Completions("not json"))
    verdict = asyncio.run(Evaluator.groundedness_judge("anything", "context", llm))
    assert verdict["grounded"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_evaluator_groundedness.py -v --tb=short -p no:logging`
Expected: FAIL with `AttributeError: type object 'Evaluator' has no attribute 'groundedness_judge'`

- [ ] **Step 3: Write the implementation**

Add to `benchmark/evaluator.py`, inside the `Evaluator` class, after the existing `llm_judge` static method:

```python
    @staticmethod
    async def groundedness_judge(
        response: str, retrieved_context: str, llm_client: "AsyncOpenAI | None" = None,
    ) -> dict:
        """Judge whether ``response`` is grounded in ``retrieved_context`` (spec §4.6).

        Returns ``{"grounded": bool | None, "hallucinated_claims": list[str],
        "answered_without_evidence": bool}``. ``grounded`` is ``None`` when no
        judge could run (no ``llm_client``, or the call/parse failed) — unknown,
        not false (spec §6: judge failures degrade rather than raising).
        Independent of ``expected_fact`` correctness: a response can be
        lexically correct yet contain an unsupported extra claim, or vice versa.
        """
        if llm_client is None:
            return {"grounded": None, "hallucinated_claims": [], "answered_without_evidence": False}
        from config import settings
        from utils.llm_utils import extract_json
        system = (
            "You are a strict fact-checking grader. Compare the RESPONSE against "
            "the RETRIEVED_CONTEXT (the only memory the assistant had access to). "
            "Reply with ONLY a JSON object: "
            '{"grounded": true/false, "hallucinated_claims": ["..."], '
            '"answered_without_evidence": true/false}. hallucinated_claims lists '
            "any specific claims in RESPONSE not supported by RETRIEVED_CONTEXT. "
            "answered_without_evidence is true when RESPONSE answers confidently "
            "despite RETRIEVED_CONTEXT being empty or irrelevant."
        )
        user = f"RETRIEVED_CONTEXT:\n{retrieved_context or '(empty)'}\n\nRESPONSE:\n{response}"
        try:
            r = await llm_client.chat.completions.create(
                model=settings.MODEL_NAME,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=0.0, max_tokens=300,
            )
            parsed = extract_json(r.choices[0].message.content or "")
            return {
                "grounded": bool(parsed.get("grounded", False)),
                "hallucinated_claims": list(parsed.get("hallucinated_claims") or []),
                "answered_without_evidence": bool(parsed.get("answered_without_evidence", False)),
            }
        except Exception as exc:  # noqa: BLE001 — judge failure must not crash a run
            log.warning("groundedness_judge failed: %s", exc)
            return {"grounded": None, "hallucinated_claims": [], "answered_without_evidence": False}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_evaluator_groundedness.py -v --tb=short -p no:logging`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add benchmark/evaluator.py tests/test_evaluator_groundedness.py
git commit -m "feat(benchmark): add Evaluator.groundedness_judge for hallucination detection"
```

---

### Task 7: Full-cycle warm/cold harness — `benchmark/conversation_runner.py`

**Files:**
- Modify: `benchmark/runner.py` (rename `_snapshot`→`snapshot_analytics`, `_diff`→`diff_analytics`; add `run_conversation` delegator method)
- Create: `benchmark/conversation_runner.py`
- Test: `tests/test_conversation_runner.py`

**Interfaces:**
- Consumes: `benchmark.runner.snapshot_analytics(analytics) -> dict`, `benchmark.runner.diff_analytics(before, after, latency, response, error, context_blocks=None) -> dict` (renamed this task); `Orchestrator.run(intent, chat_id, *, on_context_assembled=None) -> str` and `Orchestrator.drain_background(timeout=5.0) -> None` (existing); `MemoryLayers.store_episodic(chat_id, content, ...) -> str` (existing); `Evaluator.groundedness_judge` (Task 6). Mined-case shape from Task 3: `case["query"]`, `case.get("lead_in_turns")`, `case["expected_fact"]`.
- Produces: `benchmark.conversation_runner.run_conversation(orchestrator, registry, case: dict) -> dict` returning `{"warm": {...}, "cold": {...}}`, each turn dict carrying `response`, `chat_id`, `groundedness`, plus everything `diff_analytics` already returns (`latency`, `warm_served`, `context_blocks`, `cache_hit`, `prefetch_attempted`, `results_found`, `source_tier`, etc.); `BenchmarkRunner.run_conversation(case: dict) -> dict` (thin delegator on the existing class).

- [ ] **Step 1: Rename `_snapshot`/`_diff` to public names in `benchmark/runner.py`**

In `benchmark/runner.py`, rename the two module-level function definitions (only the `def` line changes on each — bodies stay exactly as they are today):

```python
def snapshot_analytics(analytics) -> dict:
    """Read the analytics aggregator's raw counters as a flat dict."""
    return {
        "cache_hits": analytics.cache_hits,
        "cache_misses": analytics.cache_misses,
        "pf_attempts": analytics.total_prefetch_attempts,
        "pf_successes": analytics.total_prefetch_successes,
        "pf_timeouts": analytics.total_prefetch_timeouts,
        "tok_inj": analytics.total_tokens_injected,
        "tok_l0l1": analytics.total_tokens_l0_l1,
        "tok_l2": analytics.total_tokens_l2,
        "tok_l3": analytics.total_tokens_l3,
        "res_found": analytics.total_results_found,
        "res_used": analytics.total_results_used,
        "tier_hits": dict(analytics.tier_hits),
        "warm_served_turns": analytics.warm_served_turns,
    }


def diff_analytics(
    before: dict, after: dict, latency: float, response: str, error: str | None,
    context_blocks: list[str] | None = None,
) -> dict:
    """Compute one run's contribution by differencing two analytics snapshots.

    ``context_blocks`` is the raw L3 text captured via Orchestrator.run's
    on_context_assembled callback — a side channel, not derived from the
    analytics counter diff (MemoryAnalytics has no raw-text field to diff).
    """
    tiers = set(after["tier_hits"]) | set(before["tier_hits"])
    new_tier = next(
        (k for k in tiers if after["tier_hits"].get(k, 0) - before["tier_hits"].get(k, 0) > 0),
        "",
    )
    return {
        "latency": latency, "response": response, "error": error,
        "cache_hit": bool(after["cache_hits"] - before["cache_hits"]),
        "cache_miss": bool(after["cache_misses"] - before["cache_misses"]),
        "prefetch_attempted": bool(after["pf_attempts"] - before["pf_attempts"]),
        "prefetch_succeeded": bool(after["pf_successes"] - before["pf_successes"]),
        "prefetch_timeout": bool(after["pf_timeouts"] - before["pf_timeouts"]),
        "results_found": after["res_found"] - before["res_found"],
        "results_used": after["res_used"] - before["res_used"],
        "tokens_injected": after["tok_inj"] - before["tok_inj"],
        "tokens_l0_l1": after["tok_l0l1"] - before["tok_l0l1"],
        "tokens_l2": after["tok_l2"] - before["tok_l2"],
        "tokens_l3": after["tok_l3"] - before["tok_l3"],
        "source_tier": new_tier,
        "warm_served": bool(after["warm_served_turns"] - before["warm_served_turns"]),
        "context_blocks": context_blocks or [],
    }
```

And update `run_single`'s two call sites:

```python
        for _ in range(repeat):
            before = snapshot_analytics(analytics)
            t0 = time.time()
            captured_blocks: list[list[str]] = []
            try:
                response = await self._orchestrator.run(
                    test_case["query"], chat_id,
                    on_context_assembled=captured_blocks.append,
                )
            except Exception as exc:  # noqa: BLE001 — one failed case must not abort the run
                error = str(exc)
                response = ""
                log.warning("orchestrator.run failed case=%s: %s", test_case.get("id"), exc)
            latency = time.time() - t0
            context_blocks = captured_blocks[-1] if captured_blocks else []
            per_run.append(diff_analytics(before, snapshot_analytics(analytics), latency, response, error, context_blocks))
```

- [ ] **Step 2: Run the full suite to confirm the rename broke nothing**

Run: `python3 -m pytest tests/ -q -p no:logging`
Expected: same pass count as before this step (no test references `_snapshot`/`_diff` by name directly — confirmed via `grep -rn "_snapshot\|_diff" tests/`)

- [ ] **Step 3: Write the failing tests for `conversation_runner`**

```python
"""tests.test_conversation_runner — full-cycle warm vs cold benchmark turns (spec §4.4)."""
from __future__ import annotations

import asyncio

from benchmark.conversation_runner import run_conversation
from memory.analytics import MemoryAnalytics
from orchestrator.orchestrator import Orchestrator
from tests._orch_fakes import _Completions, _FakeLayers, _FakeRegistry, _LLMClient


def _make_orchestrator(layers, reply="ok"):
    # A real MemoryAnalytics is required here (not _orch_fakes' _FakeAnalytics):
    # snapshot_analytics reads counters (cache_hits, warm_served_turns, ...)
    # that _FakeAnalytics doesn't define — same reasoning as
    # tests/test_benchmark_context_capture.py.
    registry = _FakeRegistry(layers, _LLMClient(_Completions(reply)), MemoryAnalytics())
    orch = Orchestrator(
        layers=registry.memory_layers, llm_client=registry.llm_client,
        plugin_manager=registry.plugin_manager, analytics=registry.memory_analytics, tools=[],
    )
    return orch, registry


def test_run_conversation_uses_distinct_chat_ids_for_warm_and_cold():
    layers = _FakeLayers()
    orch, registry = _make_orchestrator(layers)
    case = {"id": "c1", "query": "what time is the meeting", "expected_fact": "9am", "lead_in_turns": ["The meeting is at 9am."]}
    result = asyncio.run(run_conversation(orch, registry, case))
    assert result["warm"]["chat_id"] != result["cold"]["chat_id"]


def test_run_conversation_preloads_lead_in_via_store_episodic():
    layers = _FakeLayers()
    orch, registry = _make_orchestrator(layers)
    case = {"id": "c1", "query": "what time is the meeting", "expected_fact": "9am", "lead_in_turns": ["The meeting is at 9am."]}
    asyncio.run(run_conversation(orch, registry, case))
    assert layers.archive_calls >= 1  # store_episodic was called for the lead-in


def test_run_conversation_returns_response_context_blocks_and_groundedness_for_both_paths():
    layers = _FakeLayers(blocks=["[Identity]\nYou are GOAT.", "[Recall]\nThe meeting is at 9am."])
    orch, registry = _make_orchestrator(layers, reply="It's at 9am.")
    case = {"id": "c1", "query": "what time is the meeting", "expected_fact": "9am", "lead_in_turns": ["The meeting is at 9am."]}
    result = asyncio.run(run_conversation(orch, registry, case))
    for path in ("warm", "cold"):
        turn = result[path]
        assert turn["response"] == "It's at 9am."
        assert isinstance(turn["context_blocks"], list) and turn["context_blocks"]
        assert isinstance(turn["warm_served"], bool)
        assert "grounded" in turn["groundedness"]


def test_run_conversation_defaults_lead_in_to_expected_fact_when_absent():
    layers = _FakeLayers()
    orch, registry = _make_orchestrator(layers)
    case = {"id": "c2", "query": "q", "expected_fact": "the fact"}  # no lead_in_turns key
    result = asyncio.run(run_conversation(orch, registry, case))
    assert result["warm"]["response"] == "ok"
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_conversation_runner.py -v --tb=short -p no:logging`
Expected: FAIL with `ModuleNotFoundError: No module named 'benchmark.conversation_runner'`

- [ ] **Step 5: Write the implementation**

```python
"""benchmark.conversation_runner — full-cycle warm/cold conversation benchmark (spec §4.4).

Exercises the passive warm-serving path end-to-end: preload -> orchestrator.run()
(fires the post-turn prefetch daemon) -> drain_background() -> orchestrator.run()
again (now warm-served) -- vs. a cold baseline with no prior turn and no drain.
Reuses BenchmarkRunner's snapshot_analytics/diff_analytics (benchmark.runner)
so context_blocks/warm_served are captured the same way run_single captures
them, and scores each turn's response with Evaluator.groundedness_judge.
"""
from __future__ import annotations

import time
import uuid

from benchmark.evaluator import Evaluator
from benchmark.runner import diff_analytics, snapshot_analytics

__all__ = ["run_conversation"]


async def run_conversation(orchestrator, registry, case: dict) -> dict:
    """Run a mined case's warm and cold paths; return both turns' captured data."""
    warm = await _run_turn_warm(orchestrator, registry, case)
    cold = await _run_turn_cold(orchestrator, registry, case)
    return {"warm": warm, "cold": cold}


async def _run_one_turn(orchestrator, registry, chat_id: str, query: str) -> dict:
    """Run a single orchestrator turn; capture response, latency, context_blocks,
    warm_served (via diff_analytics), and the groundedness verdict."""
    analytics = registry.memory_analytics
    before = snapshot_analytics(analytics)
    captured: list[list[str]] = []
    t0 = time.time()
    response = await orchestrator.run(query, chat_id, on_context_assembled=captured.append)
    latency = time.time() - t0
    blocks = captured[-1] if captured else []
    diff = diff_analytics(before, snapshot_analytics(analytics), latency, response, None, blocks)
    verdict = await Evaluator.groundedness_judge(response, "\n\n".join(blocks), registry.llm_client)
    return {"response": response, "chat_id": chat_id, "groundedness": verdict, **diff}


async def _run_turn_warm(orchestrator, registry, case: dict) -> dict:
    """Preload lead-in content, run turn 1, drain the prefetch daemon, run turn 2 (warm)."""
    layers = registry.memory_layers
    chat_id = f"bench-warm-{uuid.uuid4().hex[:12]}"
    lead_in = case.get("lead_in_turns") or [case["expected_fact"]]
    for content in lead_in:
        await layers.store_episodic(chat_id, content)
    await orchestrator.run(lead_in[-1], chat_id)
    await orchestrator.drain_background()
    return await _run_one_turn(orchestrator, registry, chat_id, case["query"])


async def _run_turn_cold(orchestrator, registry, case: dict) -> dict:
    """Same query, brand-new chat_id, no lead-in, no drain — a single cold turn."""
    chat_id = f"bench-cold-{uuid.uuid4().hex[:12]}"
    return await _run_one_turn(orchestrator, registry, chat_id, case["query"])
```

- [ ] **Step 6: Add the thin delegator to `BenchmarkRunner`**

In `benchmark/runner.py`, add this method to the `BenchmarkRunner` class (after `run_dataset`):

```python
    async def run_conversation(self, case: dict) -> dict:
        """Full-cycle warm vs. cold benchmark for one mined case (spec §4.4)."""
        from benchmark.conversation_runner import run_conversation as _run_conversation
        return await _run_conversation(self._orchestrator, self._registry, case)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_conversation_runner.py -v --tb=short -p no:logging`
Expected: 4 passed

- [ ] **Step 8: Run the full suite**

Run: `python3 -m pytest tests/ -q -p no:logging`
Expected: all green, no regressions

- [ ] **Step 9: Commit**

```bash
git add benchmark/runner.py benchmark/conversation_runner.py tests/test_conversation_runner.py
git commit -m "feat(benchmark): add full-cycle warm/cold conversation_runner with groundedness scoring"
```

---

## Final Verification

- [ ] Run the entire suite once more: `python3 -m pytest tests/ -q -p no:logging` — expect all green.
- [ ] Confirm `.gitignore` has both `chroma_data_benchmark/` and `benchmark/data/` (Task 1, Step 5).
- [ ] Update `docs/superpowers/specs/2026-07-08-real-data-prefetch-benchmark-design.md` §4 components' status if the spec tracks implementation status (optional — check current spec conventions first).
