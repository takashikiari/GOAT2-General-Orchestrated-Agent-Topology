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
