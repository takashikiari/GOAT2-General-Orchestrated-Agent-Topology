#!/usr/bin/env python3
"""Direct SQLite query of ChromaDB to find all entries, especially old/unindexed ones."""
import sqlite3
import os
import json
import sys

workspace = os.path.dirname(os.path.abspath(__file__))
chroma_db_path = os.path.join(workspace, "memory", "chroma_db", "chroma.sqlite3")

if not os.path.exists(chroma_db_path):
    print(f"ERROR: ChromaDB SQLite not found at {chroma_db_path}")
    sys.exit(1)

print(f"=== ChromaDB SQLite: {chroma_db_path} ===")
print(f"Size: {os.path.getsize(chroma_db_path)} bytes")
print()

conn = sqlite3.connect(chroma_db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

# List all tables
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [row["name"] for row in cursor.fetchall()]
print(f"Tables ({len(tables)}): {tables}")
print()

# Explore each table
for table in tables:
    cursor.execute(f"PRAGMA table_info({table})")
    cols = cursor.fetchall()
    col_names = [c["name"] for c in cols]
    cursor.execute(f"SELECT COUNT(*) FROM {table}")
    count = cursor.fetchone()[0]
    print(f"  Table: {table} — {count} rows — columns: {col_names}")
    
    if count > 0 and count < 100:
        cursor.execute(f"SELECT * FROM {table} LIMIT 20")
        rows = cursor.fetchall()
        for row in rows:
            d = dict(row)
            # Truncate long values
            for k, v in d.items():
                if isinstance(v, str) and len(v) > 200:
                    d[k] = v[:200] + "..."
            print(f"    {json.dumps(d, default=str)}")
    print()

# Specifically look at embedding collections and their segments
print("=== Looking for collections ===")
cursor.execute("SELECT * FROM collections")
cols = cursor.fetchall()
for col in cols:
    print(f"  Collection: id={col['id']}, name={col['name']}, metadata={col['metadata']}")
print()

# Look at embedding metadata
print("=== Looking at embedding_metadata ===")
cursor.execute("SELECT * FROM embedding_metadata LIMIT 10")
rows = cursor.fetchall()
for row in rows:
    d = dict(row)
    for k, v in d.items():
        if isinstance(v, str) and len(v) > 300:
            d[k] = v[:300] + "..."
    print(f"  {json.dumps(d, default=str)}")
print()

# Count total embeddings
cursor.execute("SELECT COUNT(*) FROM embeddings")
total_embeddings = cursor.fetchone()[0]
print(f"Total embeddings: {total_embeddings}")
print()

# Sample some embeddings to see what's stored
cursor.execute("SELECT id, segment_id, seq_id FROM embeddings LIMIT 10")
rows = cursor.fetchall()
for row in rows:
    print(f"  Embedding: id={row['id']}, segment_id={row['segment_id']}, seq_id={row['seq_id']}")
print()

# Check max_document_id or similar
cursor.execute("SELECT * FROM max_document_id")
rows = cursor.fetchall()
for row in rows:
    print(f"  max_document_id: {dict(row)}")
print()

# Check segments
cursor.execute("SELECT * FROM segments")
rows = cursor.fetchall()
for row in rows:
    d = dict(row)
    for k, v in d.items():
        if isinstance(v, str) and len(v) > 200:
            d[k] = v[:200] + "..."
    print(f"  Segment: {json.dumps(d, default=str)}")
print()

conn.close()
print("=== Done ===")
