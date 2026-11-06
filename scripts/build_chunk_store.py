"""Build the Phase 1 chunk store (memory spec v4) from a COPY of the L3 source collection.

    python3 scripts/build_chunk_store.py --source ./chroma_data --dest ./chroma_chunks

The source is copied to a temp dir first, so the live database is never opened by a second process.
Idempotent (upsert by chunk id). Writes only to --dest; enable it afterwards with chunks_write/chunks_search
in config/memory.toml. Entries written after the build are caught up at bot startup (chunks_write = true).
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memory.config import CHUNK_COLLECTION_NAME, EPISODIC_COLLECTION_NAME  # noqa: E402
from memory.episodic.chunks import ChunkStore  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", default="./chroma_data")
    ap.add_argument("--dest", default="./chroma_chunks")
    ap.add_argument("--collection", default=EPISODIC_COLLECTION_NAME)
    a = ap.parse_args(argv)
    import chromadb
    from chromadb.config import Settings
    t0 = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "src"
        shutil.copytree(a.source, copy)
        src = chromadb.PersistentClient(path=str(copy), settings=Settings(anonymized_telemetry=False))
        r = src.get_collection(a.collection).get(include=["documents", "metadatas"])
        store = ChunkStore(path=a.dest, collection=CHUNK_COLLECTION_NAME)
        have = store.parent_ids_sync()  # resumable: parents already chunked are skipped
        print(f"source: {len(r['ids'])} parents, already in store: {len(have)}", flush=True)
        n_parents = n_chunks = 0
        for doc_id, doc, meta in zip(r["ids"], r["documents"], r["metadatas"]):
            if doc_id in have:
                continue
            n_chunks += store.add_sync(doc_id, doc or "", meta or {})
            n_parents += 1
            if n_parents % 250 == 0:
                print(f"  {n_parents} parents → {n_chunks} chunks ({time.time() - t0:.0f}s)", flush=True)
    print(f"done: {n_parents} parents → {n_chunks} chunks in {time.time() - t0:.0f}s → {a.dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
