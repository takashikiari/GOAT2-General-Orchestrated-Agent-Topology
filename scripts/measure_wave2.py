"""Offline measurement for Phase 2 (wave 2) on real queries, each evaluated as of its own time (read-only).

    GOAT_LOG_DIR=/tmp/x python3 scripts/measure_wave2.py --source ./chroma_data --chunks ./chroma_chunks
Highlights pipeline as live (whole-doc candidates → closest chunk → rerank → ±1 window), then wave 2 through
the real code (memory.wave2.add_neighbors + EpisodicMemory.neighbors/highlight/expand_chunks), then the gap
filter and the L3 budget. Reports neighbours added/kept, L3 tokens, lookup time, and seed-selection changes.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import statistics as st
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("GOAT_LOG_DIR", str(Path(tempfile.gettempdir()) / "goat2-offline-logs"))

from memory.budget import estimate_tokens  # noqa: E402
from memory.config import CHUNK_COLLECTION_NAME, EPISODIC_COLLECTION_NAME  # noqa: E402
from memory.context_assembler import blended_gap_filter, fit_search_results  # noqa: E402
from memory.episodic.chunks import ChunkStore  # noqa: E402
from memory.episodic.episodic import EpisodicMemory  # noqa: E402
from memory.reranker import CrossEncoderReranker  # noqa: E402
from memory.wave2 import add_neighbors  # noqa: E402
from scripts.compare_chunks import as_of, real_queries, rows  # noqa: E402

L3_BUDGET = 2500


async def main_async(a) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copytree(a.source, Path(tmp) / "src")
        mem = EpisodicMemory(storage_path=str(Path(tmp) / "src"),
                             chunks=ChunkStore(path=a.chunks, collection=CHUNK_COLLECTION_NAME))
        col = mem._get_collection()
        rr = CrossEncoderReranker()
        rr._load_and_prime()
        res = []
        for q, ts in real_queries(a.queries):
            base = as_of(ts, rows(col.query(query_texts=[q], n_results=60)))[:20]
            ranked = await mem.expand_chunks(await rr.rerank(q, await mem.highlight(q, base), top_k=len(base)))
            plain = blended_gap_filter([dict(r) for r in ranked])
            t0 = time.perf_counter()
            grown = await add_neighbors(mem, ranked, a.seeds, a.per_seed, a.window)
            extra = [n for n in grown[len(ranked):] if float((n["metadata"] or {}).get("timestamp") or 0) < ts]
            extra = await mem.expand_chunks(await mem.highlight(q, extra)) if extra else []
            ms = (time.perf_counter() - t0) * 1000
            kept = blended_gap_filter([dict(r) for r in ranked] + extra)
            b0, u0 = fit_search_results(plain, L3_BUDGET)
            b1, u1 = fit_search_results(kept, L3_BUDGET)
            seeds_plain = [r["metadata"].get("message_id") for r in plain]
            seeds_kept = [r["metadata"].get("message_id") for r in kept if "wave2" not in r.get("mechanisms", [])]
            res.append({"added": len(extra), "kept_nb": sum("wave2" in r.get("mechanisms", []) for r in kept[:u1]),
                        "tok0": estimate_tokens(b0), "tok1": estimate_tokens(b1), "ms": ms,
                        "same_seeds": seeds_plain == seeds_kept})
        n = len(res)
        print(f"queries: {n} · seeds={a.seeds} per_seed={a.per_seed} window=±{a.window}")
        print(f"  neighbours looked up per query: median {st.median(r['added'] for r in res):.0f}, "
              f"turns with ≥1: {sum(r['added'] > 0 for r in res)}/{n}")
        print(f"  neighbours injected (after gap filter + budget): median {st.median(r['kept_nb'] for r in res):.0f}, "
              f"total {sum(r['kept_nb'] for r in res)}")
        print(f"  L3 tokens injected median: {st.median(r['tok0'] for r in res):.0f} → {st.median(r['tok1'] for r in res):.0f} "
              f"(p90 {sorted(r['tok1'] for r in res)[int(0.9 * (n - 1))]})")
        print(f"  wave-2 lookup time median {st.median(r['ms'] for r in res):.0f} ms (p90 {sorted(r['ms'] for r in res)[int(0.9 * (n - 1))]:.0f})")
        print(f"  seed selection unchanged: {sum(r['same_seeds'] for r in res)}/{n}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", default="./chroma_data")
    ap.add_argument("--chunks", default="./chroma_chunks")
    ap.add_argument("--queries", type=int, default=40)
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--per-seed", type=int, default=2)
    ap.add_argument("--window", type=int, default=2)
    return asyncio.run(main_async(ap.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
