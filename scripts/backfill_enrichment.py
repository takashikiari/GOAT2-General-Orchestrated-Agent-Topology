"""scripts.backfill_enrichment — retroactively enrich existing L3 entries.

Real corpus measurement (2026-07-08): write-time enrichment (memory/layers.py
store_episodic) only ever affects entries written after that code runs. It
does nothing for entries already sitting in the collection — confirmed via
direct measurement: 9.8% entity coverage before and after the fix shipped,
unchanged, because the bot had made zero new writes. backfill_enrichment runs
the same enrich_l3_entry on a batch of already-stored entries; update_metadata
merges into existing metadata (content/chat_id/timestamp/tags untouched).

Safety: requires an explicit --path (no default toward the live collection)
and a --yes confirmation before writing anything. Safe to interrupt and
re-run — each entry is enriched independently and update_metadata is a merge,
not a replace.

Usage:
    python3 -m scripts.backfill_enrichment --path chroma_data_benchmark --yes
    python3 -m scripts.backfill_enrichment --path chroma_data --limit 50 --yes
"""
from __future__ import annotations

import asyncio

from memory.enrichment import enrich_l3_entry

__all__ = ["backfill_enrichment"]


async def backfill_enrichment(
    episodic, extractor, entries: list[dict], concurrency: int = 4,
) -> dict:
    """Enrich every entry in ``entries`` (id/content/metadata dicts), updating
    stored metadata in place. Returns ``{"total": int}``.

    Bounded concurrency (a semaphore, not unbounded asyncio.gather) — GLiNER
    inference is CPU-bound; unbounded concurrency thrashes rather than speeds
    things up. enrich_l3_entry already degrades on internal failure without
    raising, so one bad entry never aborts the batch.
    """
    sem = asyncio.Semaphore(concurrency)

    async def _one(entry: dict) -> None:
        async with sem:
            await enrich_l3_entry(entry["id"], entry.get("content", ""), "", episodic, extractor)

    await asyncio.gather(*(_one(e) for e in entries))
    return {"total": len(entries)}


async def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Retroactively enrich existing L3 entries.")
    ap.add_argument("--path", required=True, help="ChromaDB path to enrich (no default — be explicit)")
    ap.add_argument("--limit", type=int, default=None, help="cap the number of entries processed")
    ap.add_argument("--concurrency", type=int, default=4, help="concurrent enrichment calls (default 4)")
    ap.add_argument("--yes", action="store_true", help="confirm the write; without it, only reports what would run")
    args = ap.parse_args()

    from memory.config import EPISODIC_COLLECTION_NAME
    from memory.episodic import EpisodicMemory
    from memory.gliner_extractor import GLiNERExtractor
    from scripts.run_real_data_benchmark import _load_snapshot_entries

    entries = _load_snapshot_entries(args.path, EPISODIC_COLLECTION_NAME)
    if args.limit:
        entries = entries[:args.limit]
    print(f"{len(entries)} entries loaded from {args.path!r}")

    if not args.yes:
        print("dry run (no --yes given) — nothing written. Re-run with --yes to enrich.")
        return 0

    episodic = EpisodicMemory(storage_path=args.path)
    extractor = GLiNERExtractor()
    result = await backfill_enrichment(episodic, extractor, entries, concurrency=args.concurrency)
    print(f"enrichment attempted on {result['total']} entries -> {args.path!r}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(asyncio.run(main()))
