"""Phase 1 (memory spec v4): chunking, the separate chunk store, sync with the source collection, ±1 windows."""
import asyncio
import hashlib

import pytest

from memory.budget import estimate_tokens
from memory.episodic import episodic as ep_mod
from memory.episodic.chunks import ChunkStore, chunk_records, chunk_spans, window_text
from memory.episodic.episodic import EpisodicMemory

TEXT = " ".join(f"cuvant{i} despre memoria lui GOAT si Laris" for i in range(220))


class _EF:
    """Deterministic, offline embedding: hashed bag of words (enough for Chroma to store/query)."""

    def __call__(self, input):  # noqa: A002 - Chroma's protocol name
        out = []
        for text in input:
            v = [0.0] * 32
            for w in str(text).lower().split():
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 32] += 1.0
            out.append(v)
        return out

    def name(self):
        return "test-ef"

    def embed_query(self, input):  # noqa: A002
        return self(input)

    def embed_documents(self, input):  # noqa: A002
        return self(input)


def test_spans_cover_text_with_bounded_size_and_overlap():
    spans = chunk_spans(TEXT, target=220, overlap=40)
    words = TEXT.split()
    assert spans[0][0] == 0 and spans[-1][1] == len(words)
    for (a, b), (c, d) in zip(spans, spans[1:]):
        assert c < b and c > a  # overlap, but always progress
    for a, b in spans:
        assert estimate_tokens(" ".join(words[a:b])) <= 300
    assert len(spans) > 5


def test_short_text_is_one_chunk():
    assert chunk_spans("salut Laris") == [(0, 2)]
    assert chunk_spans("") == [(0, 0)]


@pytest.mark.parametrize("content", [None, "", "   \n\t "])
def test_empty_parent_gives_no_chunks(content):
    """Regression for the highlights evaluation (Q2): a parent with None/blank content produced an empty
    chunk that won the rerank on a short query."""
    assert chunk_records("doc-empty", content, {"timestamp": 1.0}) == []


def test_records_inherit_parent_metadata():
    meta = {"topic_id": "t1", "entities": "Laris,GOAT", "sequence_number": 7, "chat_id": "c",
            "timestamp": 1.0, "nested": {"x": 1}}
    recs = chunk_records("doc1", TEXT, meta)
    assert all(r[0].startswith("doc1#") for r in recs)
    for i, (_, _, m) in enumerate(recs):
        assert m["parent_id"] == "doc1" and m["message_id"] == "doc1" and m["chunk_index"] == i
        assert m["topic_id"] == "t1" and m["entities"] == "Laris,GOAT" and m["sequence_number"] == 7
        assert "nested" not in m and m["n_chunks"] == len(recs)


def test_window_is_neighbours_without_duplicated_overlap():
    spans = chunk_spans(TEXT)
    words = TEXT.split()
    w = window_text(TEXT, 2)
    assert w == " ".join(words[spans[1][0]:spans[3][1]])
    assert window_text(TEXT, 0) == " ".join(words[spans[0][0]:spans[1][1]])
    assert window_text("scurt", 5) == "scurt"


@pytest.fixture
def mem(tmp_path, monkeypatch):
    monkeypatch.setattr(ep_mod, "CHUNKS_WRITE", True)
    monkeypatch.setattr(ep_mod, "CHUNKS_SEARCH", True)
    import memory.config as cfg
    monkeypatch.setattr(cfg, "CHUNKS_SEARCH", True)
    m = EpisodicMemory(storage_path=str(tmp_path / "src"),
                       chunks=ChunkStore(path=str(tmp_path / "chunks"), embedding_function=_EF()))
    m._chunks.lock = m._write_lock
    import chromadb
    from chromadb.config import Settings
    client = chromadb.PersistentClient(path=str(tmp_path / "src"), settings=Settings(anonymized_telemetry=False))
    m._collection = client.get_or_create_collection("episodic_memory", embedding_function=_EF())
    return m


def test_store_enrich_delete_stay_in_sync(mem):
    doc = asyncio.run(mem.store("c", TEXT, {"timestamp": 1.0, "topic_id": "t1"}))
    n = len(chunk_spans(TEXT))
    assert len(mem._chunks.collection().get(where={"parent_id": {"$eq": doc}})["ids"]) == n
    asyncio.run(mem.update_metadata(doc, {"entities": "Laris"}))
    metas = mem._chunks.collection().get(where={"parent_id": {"$eq": doc}}, include=["metadatas"])["metadatas"]
    assert all(m["entities"] == "Laris" for m in metas)
    asyncio.run(mem.delete_entries([doc]))
    assert mem._chunks.collection().get(where={"parent_id": {"$eq": doc}})["ids"] == []


def test_search_returns_chunks_and_expand_rebuilds_window(mem):
    doc = asyncio.run(mem.store("c", TEXT, {"timestamp": 1.0}))
    hits = asyncio.run(mem.search("cuvant5 memoria", limit=3))
    assert hits and all(h["metadata"]["parent_id"] == doc for h in hits)
    assert all(estimate_tokens(h["content"]) <= 300 for h in hits)
    out = asyncio.run(mem.expand_chunks(hits[:1]))
    i = hits[0]["metadata"]["chunk_index"]
    assert out[0]["content"] == window_text(TEXT, i) and out[0]["metadata"]["chunk_expanded"]


def test_bm25_indexes_chunks_when_searching_chunks(mem):
    asyncio.run(mem.store("c", TEXT, {"timestamp": 1.0}))
    rows = asyncio.run(mem.get_all_for_index())
    assert rows and all("#" in r["id"] for r in rows)


def test_catch_up_chunks_entries_written_before(mem, monkeypatch):
    monkeypatch.setattr(ep_mod, "CHUNKS_WRITE", False)
    doc = asyncio.run(mem.store("c", "un text scurt despre Letta", {"timestamp": 1.0}))
    assert mem._chunks.parent_ids_sync() == set()
    assert mem._sync_missing_chunks() == 1 and mem._chunks.parent_ids_sync() == {doc}


def test_layers_rerank_expands_only_in_chunk_search_mode(monkeypatch):
    import memory.config as cfg
    from memory.layers import MemoryLayers

    class _R:
        async def rerank(self, q, results):
            return list(reversed(results))

    class _E:
        async def expand_chunks(self, results):
            return [dict(r, content=r["content"] + " +ctx") for r in results]

    layers = object.__new__(MemoryLayers)
    layers._reranker, layers._episodic = _R(), _E()
    res = [{"content": "a"}, {"content": "b"}]
    # pin the flags this test is not about: the repo's memory.toml may enable T7/wave2
    monkeypatch.setattr(cfg, "L3_MIN_ALNUM", 0)
    monkeypatch.setattr(cfg, "L3_WAVE2", False)
    monkeypatch.setattr(cfg, "CHUNKS_HIGHLIGHTS", False)
    monkeypatch.setattr(cfg, "CHUNKS_SEARCH", False)
    assert [r["content"] for r in asyncio.run(layers.rerank("q", res))] == ["b", "a"]
    monkeypatch.setattr(cfg, "CHUNKS_SEARCH", True)
    assert [r["content"] for r in asyncio.run(layers.rerank("q", res))] == ["b +ctx", "a +ctx"]


def test_store_skips_empty_parents_and_purges_old_empty_chunks(tmp_path):
    store = ChunkStore(path=str(tmp_path / "c"), embedding_function=_EF())
    assert store.add_sync("empty", None, {}) == 0 and store.add_sync("blank", "  ", {}) == 0
    assert store.collection().count() == 0
    store.collection().add(ids=["old#0"], documents=[""], metadatas=[{"parent_id": "old"}])
    store.add_sync("ok", "un text real despre Letta", {})
    assert store.purge_empty_sync() == 1 and store.collection().get()["ids"] == ["ok#0"]


def test_highlight_swaps_each_candidate_for_its_closest_chunk(mem):
    doc = asyncio.run(mem.store("c", TEXT, {"timestamp": 1.0}))
    lone = {"content": "fără chunk", "metadata": {"message_id": "absent"}}
    cand = [{"content": TEXT, "metadata": {"message_id": doc, "timestamp": 1.0}, "score": 0.3}, lone]
    out = asyncio.run(mem.highlight("cuvant7 memoria", cand))
    assert out[0]["metadata"]["parent_id"] == doc and estimate_tokens(out[0]["content"]) <= 300
    assert out[0]["content"] in TEXT and out[0]["metadata"]["timestamp"] == 1.0  # parent metadata kept
    assert out[1] == lone  # no chunk → today's behaviour for that candidate


def test_highlight_degrades_to_whole_documents_on_store_error(mem, monkeypatch):
    def boom(**kw):
        raise RuntimeError("store down")
    monkeypatch.setattr(mem._chunks, "query_sync", boom)
    cand = [{"content": TEXT, "metadata": {"message_id": "x"}}]
    assert asyncio.run(mem.highlight("q", cand)) == cand


def test_layers_rerank_highlights_then_expands(monkeypatch):
    import memory.config as cfg
    from memory.layers import MemoryLayers
    calls = []

    class _R:
        async def rerank(self, q, results):
            calls.append(("rerank", [r["content"] for r in results]))
            return results

    class _E:
        async def highlight(self, q, results):
            calls.append(("highlight",))
            return [dict(r, content=r["content"] + "-chunk") for r in results]

        async def expand_chunks(self, results):
            calls.append(("expand",))
            return results

    layers = object.__new__(MemoryLayers)
    layers._reranker, layers._episodic = _R(), _E()
    # pin the flags this test is not about: the repo's memory.toml enables T7 (min 5 alnum) and wave 2
    monkeypatch.setattr(cfg, "L3_MIN_ALNUM", 0)
    monkeypatch.setattr(cfg, "L3_WAVE2", False)
    monkeypatch.setattr(cfg, "CHUNKS_SEARCH", False)
    monkeypatch.setattr(cfg, "CHUNKS_HIGHLIGHTS", True)
    asyncio.run(layers.rerank("q", [{"content": "doc"}]))
    assert calls == [("highlight",), ("rerank", ["doc-chunk"]), ("expand",)]
    calls.clear()
    monkeypatch.setattr(cfg, "CHUNKS_HIGHLIGHTS", False)
    asyncio.run(layers.rerank("q", [{"content": "doc"}]))
    assert calls == [("rerank", ["doc"])]


def test_startup_catch_up_never_builds_a_whole_store(mem, monkeypatch):
    """chunks_write on an unbuilt store must not embed thousands of parents under the search lock."""
    monkeypatch.setattr(ep_mod, "CHUNKS_WRITE", False)
    for i in range(5):
        asyncio.run(mem.store("c", f"intrare numărul {i} despre Letta", {"timestamp": float(i)}))
    assert mem._sync_missing_chunks(limit=3) == 0 and mem._chunks.parent_ids_sync() == set()
    assert mem._sync_missing_chunks(limit=10) == 5
