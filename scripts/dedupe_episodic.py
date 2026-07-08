"""scripts.dedupe_episodic — remove exact-duplicate L3 entries from ChromaDB.

Root cause (see conversation / CHANGELOG): most duplicates are legacy rows with
no ``tags`` metadata key at all — evidence they predate the current
``store_episodic`` write path and were bulk-imported more than once. A smaller
number are live duplicates written by the current pipeline itself (same
``chat_id`` + byte-identical ``user: ... assistant: ...`` pair, consecutive
``sequence_number``, timestamps minutes apart) — caused by Telegram redelivering
an unacknowledged update after a bot restart and the handler reprocessing it
end-to-end. The redelivery guard in ``telegram_interface/bot.py`` (``SET NX``
on ``update_id``) stops new ones; this script cleans up what already exists.

Grouping key: exact ``(chat_id, content)`` match. Within a group, the entry
with the earliest ``timestamp`` (ties broken by lowest ``sequence_number``) is
kept; the rest are deleted. Byte-identical content under the same chat is, in
practice, never a coincidence — user text alone might repeat, but a full
``user: X\\nassistant: Y`` pair matching verbatim (Y is LLM-generated at
temperature > 0) effectively never happens twice by chance.

Modes
-----
* default (dry-run): report what would be deleted. No writes.
* ``--apply``: write a JSON backup of every row about to be deleted, then
  delete them from the collection.

Run: ``python3 -m scripts.dedupe_episodic`` (dry-run) or
``python3 -m scripts.dedupe_episodic --apply``. Needs only ChromaDB, no LLM.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
import uuid
from collections import defaultdict
from pathlib import Path

from memory.config import EPISODIC_COLLECTION_NAME, EPISODIC_STORAGE_PATH


def _build_client():
    import chromadb
    import posthog as _posthog
    from chromadb.config import Settings
    _posthog.disabled = True
    _posthog.capture = lambda *a, **k: None  # type: ignore[assignment]
    return chromadb.PersistentClient(
        path=EPISODIC_STORAGE_PATH,
        settings=Settings(anonymized_telemetry=False),
    )


async def _fetch_all(col) -> tuple[list[str], list[str], list[dict]]:
    def _sync():
        r = col.get(include=["documents", "metadatas"])
        ids = list(r.get("ids") or [])
        docs = list(r.get("documents") or [])
        metas = [dict(m or {}) for m in (r.get("metadatas") or [])]
        return ids, docs, metas
    return await asyncio.to_thread(_sync)


def _find_duplicate_groups(
    ids: list[str], docs: list[str], metas: list[dict],
) -> list[list[tuple[str, str, dict]]]:
    """Group rows by (chat_id, content); return only groups with >1 row.

    Each returned group is sorted keep-first: earliest timestamp, then lowest
    sequence_number.
    """
    by_key: dict[tuple[str, str], list[tuple[str, str, dict]]] = defaultdict(list)
    for i, d, m in zip(ids, docs, metas):
        by_key[(m.get("chat_id"), d)].append((i, d, m))

    groups = []
    for rows in by_key.values():
        if len(rows) < 2:
            continue
        rows.sort(key=lambda row: (
            float(row[2].get("timestamp", 0) or 0),
            int(row[2].get("sequence_number", 0) or 0),
        ))
        groups.append(rows)
    return groups


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                     help="actually delete duplicates (writes a backup first); default is dry-run")
    ap.add_argument("--backup", type=Path, default=None,
                     help="backup JSON path for --apply (default: a temp file)")
    args = ap.parse_args()

    client = _build_client()
    col = client.get_or_create_collection(EPISODIC_COLLECTION_NAME)
    print(f"collection: {EPISODIC_COLLECTION_NAME!r} at {EPISODIC_STORAGE_PATH}")
    print(f"row count:  {col.count()}")

    ids, docs, metas = await _fetch_all(col)
    groups = _find_duplicate_groups(ids, docs, metas)

    to_delete: list[tuple[str, str, dict]] = []
    for rows in groups:
        to_delete.extend(rows[1:])  # keep rows[0], drop the rest

    print(f"duplicate groups found: {len(groups)}")
    print(f"rows to delete:         {len(to_delete)}")
    print(f"rows to keep:           {len(groups)} (one per group)")

    if not to_delete:
        print("nothing to do.")
        return 0

    legacy_no_tags = sum(1 for _, _, m in to_delete if "tags" not in m)
    print(f"  of which legacy (no 'tags' key at all): {legacy_no_tags}")
    print(f"  of which written by the current pipeline: {len(to_delete) - legacy_no_tags}")

    print("\nsample (up to 5 groups):")
    for rows in groups[:5]:
        keep = rows[0]
        print(f"  KEEP {keep[0]}  ts={keep[2].get('timestamp')}  seq={keep[2].get('sequence_number')}")
        for i, _, m in rows[1:]:
            print(f"  DROP {i}  ts={m.get('timestamp')}  seq={m.get('sequence_number')}")
        print(f"  content: {rows[0][1][:120]!r}")
        print()

    if not args.apply:
        print("dry-run only — re-run with --apply to back up and delete.")
        return 0

    backup = args.backup or Path(tempfile.gettempdir()) / f"episodic_dupes_backup_{uuid.uuid4().hex[:8]}.json"
    backup.write_text(json.dumps(
        [{"id": i, "document": d, "metadata": m} for i, d, m in to_delete],
    ))
    print(f"backup written: {backup} ({len(to_delete)} rows)")

    delete_ids = [i for i, _, _ in to_delete]

    def _delete_sync():
        BATCH = 1000
        for i in range(0, len(delete_ids), BATCH):
            col.delete(ids=delete_ids[i:i + BATCH])
    await asyncio.to_thread(_delete_sync)

    new_count = col.count()
    print(f"deleted {len(delete_ids)} rows. new row count: {new_count}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(asyncio.run(main()))
