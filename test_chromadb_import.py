#!/usr/bin/env python3
"""Simple test: import ChromaBase and instantiate it."""

import sys
import os

# Ensure the workspace root is on sys.path
workspace = os.path.dirname(os.path.abspath(__file__))
if workspace not in sys.path:
    sys.path.insert(0, workspace)

print("=" * 60)
print("TEST: Import and instantiate ChromaBase")
print("=" * 60)

try:
    from memory.episodic.chromadb_base import ChromaBase
    print("[OK] Import successful: ChromaBase")
except Exception as e:
    print(f"[FAIL] Import error: {type(e).__name__}: {e}")
    sys.exit(1)

try:
    cb = ChromaBase(persist_dir="/tmp/goat2_test_chroma")
    print(f"[OK] Instantiation successful: ChromaBase(persist_dir='/tmp/goat2_test_chroma')")
    print(f"     persist_dir = {cb._persist_dir}")
    print(f"     embedding_fn = {cb._embedding_fn}")
    print(f"     _chroma = {cb._chroma}")
    print(f"     _cols = {cb._cols}")
except Exception as e:
    print(f"[FAIL] Instantiation error: {type(e).__name__}: {e}")
    sys.exit(1)

print("=" * 60)
print("RESULT: PASS - ChromaBase imported and instantiated successfully.")
print("=" * 60)
