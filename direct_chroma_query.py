#!/usr/bin/env python3
"""
Direct ChromaDB query — returns ALL entries and total count.
Uses the same PersistentClient path as the GOAT memory system.
"""
import os
import sys
import json

# Ensure we can import project modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import chromadb
from memory.chroma_types import _DEFAULT_PERSIST_DIR, _COLLECTION_PREFIX

def main():
    persist_dir = os.environ.get("CHROMA_PERSIST_DIR", _DEFAULT_PERSIST_DIR)
    print(f"ChromaDB persist dir: {persist_dir}")
    print(f"Directory exists: {os.path.isdir(persist_dir)}")
    print()

    # Connect to ChromaDB
    client = chromadb.PersistentClient(
        path=persist_dir,
        settings=chromadb.Settings(anonymized_telemetry=False),
    )

    # List ALL collections
    all_collections = client.list_collections()
    print(f"Total collections: {len(all_collections)}")
    print()

    # Filter GOAT collections
    goat_collections = [c for c in all_collections if c.name.startswith(_COLLECTION_PREFIX)]
    print(f"GOAT collections (prefix '{_COLLECTION_PREFIX}'): {len(goat_collections)}")
    print()

    if not goat_collections:
        print("No GOAT collections found.")
        return

    grand_total = 0

    for col in goat_collections:
        print(f"{'='*80}")
        print(f"  COLLECTION: '{col.name}'")
        print(f"  Metadata: {col.metadata}")
        count = col.count()
        print(f"  Document count: {count}")
        print(f"{'='*80}")
        grand_total += count

        if count == 0:
            print("  (empty collection)")
            print()
            continue

        # Get ALL documents (up to 1000)
        fetch_limit = min(count, 1000)
        try:
            result = col.get(
                limit=fetch_limit,
                include=["documents", "metadatas"],
            )
        except Exception as e:
            print(f"  ERROR fetching records: {e}")
            print()
            continue

        ids = result.get("ids", [])
        documents = result.get("documents", [])
        metadatas = result.get("metadatas", [])

        print(f"  Retrieved {len(ids)} documents (limit={fetch_limit})")
        print()

        for i, (doc_id, doc, meta) in enumerate(zip(ids, documents, metadatas)):
            print(f"  ── Entry #{i+1} ──")
            print(f"     ID:       {doc_id}")
            if meta:
                print(f"     Key:      {meta.get('key', 'N/A')}")
                print(f"     Agent:    {meta.get('agent_role', 'N/A')}")
                print(f"     Created:  {meta.get('created_at', 'N/A')}")
                print(f"     TS:       {meta.get('created_at_ts', 'N/A')}")
                print(f"     Tags:     {meta.get('tags', 'N/A')}")
            else:
                print(f"     (no metadata)")
            if doc:
                # Show full content
                print(f"     Content ({len(doc)} chars):")
                print(f"       {doc}")
            else:
                print(f"     Content: (empty)")
            print()

    # Also try query to see if there are results via semantic search
    print(f"{'='*80}")
    print(f"  GRAND TOTAL: {grand_total} documents across {len(goat_collections)} collections")
    print(f"{'='*80}")

    # Also try a semantic query on each collection
    print()
    print(f"{'='*80}")
    print(f"  SEMANTIC QUERY TEST (query='test', n_results=3 per collection)")
    print(f"{'='*80}")
    for col in goat_collections:
        count = col.count()
        if count == 0:
            continue
        try:
            q_result = col.query(
                query_texts=["test"],
                n_results=min(3, count),
                include=["documents", "metadatas", "distances"],
            )
            q_ids = q_result.get("ids", [[]])[0]
            q_docs = q_result.get("documents", [[]])[0]
            q_metas = q_result.get("metadatas", [[]])[0]
            q_dists = q_result.get("distances", [[]])[0]
            print(f"\n  Collection '{col.name}' — query results:")
            for j, (qid, qdoc, qmeta, qdist) in enumerate(zip(q_ids, q_docs, q_metas, q_dists)):
                print(f"    [{j+1}] ID: {qid}")
                print(f"        Distance: {qdist}")
                print(f"        Key: {qmeta.get('key', 'N/A') if qmeta else 'N/A'}")
                print(f"        Content: {(qdoc or '')[:100]}...")
        except Exception as e:
            print(f"  Query test for '{col.name}' failed: {e}")

    print()
    print("DONE.")


if __name__ == "__main__":
    main()
