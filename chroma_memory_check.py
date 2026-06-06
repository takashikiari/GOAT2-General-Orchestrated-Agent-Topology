#!/usr/bin/env python3
"""ChromaDB Memory Check — list collections, query records, report timestamp fields."""

import asyncio
import os
import sys
from datetime import datetime, timezone

# Ensure we can import from the project
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import chromadb
from memory.chroma_types import _DEFAULT_PERSIST_DIR, _COLLECTION_PREFIX


async def main():
    persist_dir = os.environ.get("CHROMA_PERSIST_DIR", _DEFAULT_PERSIST_DIR)
    print(f"{'='*70}")
    print(f"  CHROMADB MEMORY CHECK")
    print(f"{'='*70}")
    print(f"  Persist dir: {persist_dir}")
    print(f"  Exists:      {os.path.isdir(persist_dir)}")
    print()

    # Connect to ChromaDB
    client = chromadb.PersistentClient(
        path=persist_dir,
        settings=chromadb.Settings(anonymized_telemetry=False),
    )

    # List all collections
    all_collections = client.list_collections()
    print(f"  Total collections (all): {len(all_collections)}")

    goat_collections = [c for c in all_collections if c.name.startswith(_COLLECTION_PREFIX)]
    print(f"  GOAT collections (prefix '{_COLLECTION_PREFIX}'): {len(goat_collections)}")
    print()

    if not goat_collections:
        print("  No GOAT collections found. Nothing to inspect.")
        return

    for col in goat_collections:
        print(f"  ── Collection: '{col.name}'")
        print(f"     Metadata: {col.metadata}")
        count = col.count()
        print(f"     Document count: {count}")

        if count == 0:
            print()
            continue

        # Fetch a few records to inspect their metadata
        fetch_limit = min(5, count)
        try:
            result = col.get(
                limit=fetch_limit,
                include=["documents", "metadatas"],
            )
        except Exception as e:
            print(f"     ERROR fetching records: {e}")
            print()
            continue

        ids = result.get("ids", [])
        documents = result.get("documents", [])
        metadatas = result.get("metadatas", [])

        print(f"     Sample records (up to {fetch_limit}):")
        for i, (doc_id, doc, meta) in enumerate(zip(ids, documents, metadatas)):
            print(f"       [{i+1}] ID: {doc_id}")
            if meta:
                print(f"           Metadata keys: {list(meta.keys())}")
                # Check for timestamp fields
                ts_fields = []
                for k, v in meta.items():
                    if k in ("created_at", "created_at_ts", "updated_at", "timestamp", "ts"):
                        ts_fields.append((k, v, type(v).__name__))
                    elif "time" in k.lower() or "ts" in k.lower() or "date" in k.lower():
                        ts_fields.append((k, v, type(v).__name__))
                if ts_fields:
                    print(f"           Timestamp fields found:")
                    for k, v, t in ts_fields:
                        print(f"             • {k} = {v!r}  (type: {t})")
                else:
                    print(f"           No timestamp fields detected in metadata")
                # Show all metadata
                for k, v in meta.items():
                    print(f"             {k}: {v!r}")
            else:
                print(f"           No metadata")
            if doc:
                preview = doc[:120] + "..." if len(doc) > 120 else doc
                print(f"           Content preview: {preview}")
            print()

        # Also try a query to see if query results have different metadata
        if count > 0:
            try:
                q_result = col.query(
                    query_texts=["test"],
                    n_results=min(1, count),
                    include=["documents", "metadatas", "distances"],
                )
                q_metadatas = q_result.get("metadatas", [[]])
                if q_metadatas and q_metadatas[0]:
                    print(f"     Query result metadata sample:")
                    for qm in q_metadatas[0]:
                        if qm:
                            print(f"       Keys: {list(qm.keys())}")
                            for k, v in qm.items():
                                print(f"         {k}: {v!r}")
            except Exception as e:
                print(f"     Query test skipped: {e}")

        print()

    # Summary
    print(f"{'='*70}")
    print(f"  SUMMARY")
    print(f"{'='*70}")
    print(f"  Persist dir: {persist_dir}")
    print(f"  Total collections: {len(all_collections)}")
    print(f"  GOAT collections: {len(goat_collections)}")
    for col in goat_collections:
        print(f"    - {col.name}: {col.count()} documents")
    print(f"{'='*70}")


if __name__ == "__main__":
    asyncio.run(main())
