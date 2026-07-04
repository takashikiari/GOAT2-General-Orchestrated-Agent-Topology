"""scripts.repair_episodic — detect and rebuild a ChromaDB HNSW/metadata desync.

Symptom this addresses: a cold-turn prefetch logs
``prefetch mechanism raised chat=… mechanism=thematic: Error executing plan:
Internal error: Error finding id``. That is a duckdb internal error raised when
the HNSW vector index returns a neighbour id that isn't in the metadata table —
a segment/metadata desync in a ChromaDB ``PersistentClient`` (typical after a
concurrent write/delete race or an unclean shutdown). It does not reliably
self-heal.

Modes
-----
* default (check-only): probe the collection with vector queries and report
  whether the desync is present. No writes. Safe to run any time.
* ``--rebuild``: export every (id, document, metadata), write a JSON backup,
  drop + recreate the collection, re-add the exported rows verbatim (ids,
  ``access_count``, ``last_accessed_ts``, ``timestamp``, ``chat_id``, tags all
  preserved — ``store()`` is NOT used, because it regenerates ids). Then
  re-probe to confirm the desync is gone.

Run: ``source .env && python3 -m scripts.repair_episodic`` (check) or
``python3 -m scripts.repair_episodic --rebuild``. Needs only ChromaDB, no LLM.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import uuid
from pathlib import Path

from memory.config import EPISODIC_COLLECTION_NAME, EPISODIC_STORAGE_PATH

_PROBE_TEXTS = [
    "wifi password", "what is my name", "project deadline",
    "doctor appointment", "coffee order", "meeting time",
    "sister", "favorite book", "phone model", "shoe size",
]
_DESYNC_MARKERS = ("error finding id", "error executing plan")
_SAMPLE_FOR_PROBES = 30  # existing docs used as queries (find-themselves probes)


def _is_desync(exc: BaseException) -> bool:
    """True if ``exc`` looks like the HNSW/metadata id desync."""
    text = repr(exc).lower()
    return any(m in text for m in _DESYNC_MARKERS)


def _build_client():
    """Reconstruct the PersistentClient exactly as EpisodicMemory does."""
    import chromadb
    import posthog as _posthog
    from chromadb.config import Settings
    _posthog.disabled = True
    _posthog.capture = lambda *a, **k: None  # type: ignore[assignment]
    return chromadb.PersistentClient(
        path=EPISODIC_STORAGE_PATH,
        settings=Settings(anonymized_telemetry=False),
    )


async def _probe(col) -> list[str]:
    """Run vector queries; return a list of desync error strings (empty = clean)."""
    errors: list[str] = []

    def _sync() -> None:
        # Warm up the embedding fn (one-time init can flake) and gather sample
        # documents to use as find-themselves probes — a doc's own content is the
        # query most likely to reach its real neighbours and surface a stale id.
        sample_docs: list[str] = []
        try:
            got = col.get(include=["documents"], limit=_SAMPLE_FOR_PROBES)
            sample_docs = [d for d in (got.get("documents") or []) if d]
        except Exception as exc:  # noqa: BLE001
            if _is_desync(exc):
                errors.append(f"sample-get: {exc!r}")
        queries = _PROBE_TEXTS + sample_docs
        for q in queries:
            try:
                col.query(query_texts=[q], n_results=10)
            except Exception as exc:  # noqa: BLE001
                if _is_desync(exc):
                    errors.append(f"query {q!r}: {exc!r}")
                else:
                    errors.append(f"other-error query {q!r}: {exc!r}")

    await asyncio.to_thread(_sync)
    return errors


async def _export(col) -> tuple[list[str], list[str], list[dict]]:
    """Pull every (id, document, metadata) row from the collection."""
    def _sync():
        r = col.get(include=["documents", "metadatas"])
        ids = list(r.get("ids") or [])
        docs = list(r.get("documents") or [])
        metas = [dict(m or {}) for m in (r.get("metadatas") or [])]
        return ids, docs, metas
    return await asyncio.to_thread(_sync)


async def _rebuild(client, name, ids, docs, metas) -> None:
    """Drop + recreate the collection and re-add the exported rows verbatim."""
    def _sync() -> None:
        client.delete_collection(name)
        col = client.get_or_create_collection(name)
        # Re-add in batches — ChromaDB add is happiest under ~5k rows per call.
        BATCH = 1000
        for i in range(0, len(ids), BATCH):
            col.add(
                ids=ids[i:i + BATCH],
                documents=docs[i:i + BATCH],
                metadatas=metas[i:i + BATCH],
            )
    await asyncio.to_thread(_sync)


async def main() -> int:
    ap = argparse.ArgumentParser(description="ChromaDB episodic desync check/repair")
    ap.add_argument("--rebuild", action="store_true",
                    help="rebuild the collection (destructive; writes a backup first)")
    ap.add_argument("--backup", type=Path, default=None,
                    help="backup JSON path for --rebuild (default: a temp file)")
    args = ap.parse_args()

    client = _build_client()
    col = client.get_or_create_collection(EPISODIC_COLLECTION_NAME)
    print(f"collection: {EPISODIC_COLLECTION_NAME!r} at {EPISODIC_STORAGE_PATH}")
    print(f"row count:  {col.count()}")

    print("probing for HNSW/metadata desync …")
    errors = await _probe(col)
    if not errors:
        print("CHECK: clean — no 'Error finding id' desync detected.")
        if not args.rebuild:
            return 0
        print("--rebuild given but collection is clean; nothing to rebuild.")
        return 0

    print(f"CHECK: DESYNC DETECTED — {len(errors)} probe(s) raised:")
    for e in errors[:10]:
        print(f"  - {e}")
    if not args.rebuild:
        print("\nRe-run with --rebuild to export → backup → drop → recreate → re-add.")
        return 1

    ids, docs, metas = await _export(col)
    if len(ids) != col.count():
        print(f"WARNING: export rows ({len(ids)}) != count ({col.count()}); "
              f"aborting rebuild — investigate manually.")
        return 2
    backup = args.backup or Path(tempfile.gettempdir()) / f"episodic_backup_{uuid.uuid4().hex[:8]}.json"
    backup.write_text(json.dumps(
        [{"id": i, "document": d, "metadata": m} for i, d, m in zip(ids, docs, metas)],
    ))
    print(f"backup written: {backup} ({len(ids)} rows)")

    print("rebuilding: drop + recreate + re-add …")
    await _rebuild(client, EPISODIC_COLLECTION_NAME, ids, docs, metas)
    col = client.get_or_create_collection(EPISODIC_COLLECTION_NAME)
    print(f"new row count: {col.count()}")
    if col.count() != len(ids):
        print("ERROR: row count mismatch after rebuild — restore from backup.")
        return 2

    print("re-probing after rebuild …")
    post = await _probe(col)
    if post:
        print(f"REBUILD FAILED — desync still present: {post[:5]}")
        return 2
    print("REBUILD OK — desync gone; collection consistent. "
          f"Backup retained at {backup}.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))