#!/usr/bin/env python3
"""Try to use ChromaMemoryClient to list all entries in episodic memory."""
import sys
import os
import asyncio

workspace = os.path.dirname(os.path.abspath(__file__))
if workspace not in sys.path:
    sys.path.insert(0, workspace)

print("=" * 70)
print("EPISODIC MEMORY — ChromaDB Direct Access Attempt")
print("=" * 70)

async def main():
    try:
        from memory.episodic.chromadb_client import ChromaMemoryClient
        print("[OK] Imported ChromaMemoryClient")
    except Exception as e:
        print(f"[FAIL] Import error: {e}")
        return

    try:
        client = ChromaMemoryClient()
        print(f"[OK] Instantiated ChromaMemoryClient")
        print(f"     persist_dir = {client._persist_dir}")
    except Exception as e:
        print(f"[FAIL] Instantiation error: {e}")
        return

    # List all collections
    try:
        cols = await client.collections()
        print(f"\n[INFO] GOAT collections: {cols}")
    except Exception as e:
        print(f"[WARN] Could not list collections: {e}")
        cols = []

    # Try common agent roles
    roles_to_try = ["goat", "supervisor", "default", "user", "assistant"]
    if cols:
        # Extract roles from collection names
        roles_from_cols = [c.replace("goat2_", "") for c in cols if c.startswith("goat2_")]
        roles_to_try = roles_from_cols + roles_to_try

    for role in set(roles_to_try):
        try:
            count = await client.count(role)
            print(f"\n--- Agent Role: '{role}' — Count: {count} ---")
            
            if count > 0:
                # List entries
                entries = await client.list(role, limit=min(count, 50))
                print(f"  Retrieved {len(entries)} entries:")
                for i, e in enumerate(entries):
                    key = getattr(e, 'key', e.get('key', '?')) if isinstance(e, dict) else e.key
                    created = getattr(e, 'created_at', e.get('created_at', '?')) if isinstance(e, dict) else e.created_at
                    content = getattr(e, 'content', e.get('content', '')) if isinstance(e, dict) else e.content
                    meta = getattr(e, 'metadata', e.get('metadata', {})) if isinstance(e, dict) else e.metadata
                    tags = meta.get('tags', []) if isinstance(meta, dict) else []
                    permanent = meta.get('permanent', False) if isinstance(meta, dict) else False
                    print(f"  [{i+1}] key={key}")
                    print(f"       created={created}")
                    print(f"       tags={tags}, permanent={permanent}")
                    print(f"       content={str(content)[:150]}...")
                    print()
        except Exception as e:
            print(f"  [WARN] Role '{role}' error: {e}")

    # Also try health check
    try:
        healthy = await client.health()
        print(f"\n[INFO] ChromaDB health: {'OK' if healthy else 'FAIL'}")
    except Exception as e:
        print(f"[WARN] Health check error: {e}")

    print("\n" + "=" * 70)
    print("DONE")
    print("=" * 70)

asyncio.run(main())
