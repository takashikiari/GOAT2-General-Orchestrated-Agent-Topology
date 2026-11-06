"""Offline before/after for Phase 1 (memory spec v4): whole-document L3 vs chunks + ±1 window.

    python3 scripts/compare_chunks.py --source ./chroma_data --chunks ./chroma_chunks --queries 40

Read-only: copies --source to a temp dir; never touches the running bot. Per real query (the user
messages recorded by observability in goat2.log) it runs, for both variants, vector search (20) →
cross-encoder rerank → blended_gap_filter → fit into an L3 budget, and reports rerank time, the longest
pair the reranker had to pad to, L3 tokens injected, and agreement of the selected parent documents.
Approximation: BM25, entity boost and the temporal route are not part of this comparison.
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import json
import shutil
import statistics as st
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# Offline tools must never write into the live bot's /tmp/goat2/logs/goat2.log (their rerank/embedding lines
# looked like production traffic). Set before any GOAT module configures logging.
import os  # noqa: E402
os.environ.setdefault("GOAT_LOG_DIR", str(Path(tempfile.gettempdir()) / "goat2-offline-logs"))

from memory.config import CHUNK_COLLECTION_NAME, EPISODIC_COLLECTION_NAME  # noqa: E402
from memory.context_assembler import blended_gap_filter, fit_search_results  # noqa: E402
from memory.episodic.chunks import window_text  # noqa: E402
from memory.reranker import CrossEncoderReranker  # noqa: E402

L3_BUDGET = 2500


def real_queries(n: int) -> list[tuple[str, float]]:
    """(text, timestamp) of real user messages, oldest first."""
    seen, out = set(), []
    for f in sorted(glob.glob("/tmp/goat2/logs/goat2.log*"), reverse=True):
        for line in open(f, errors="replace"):
            if "memory.observability" in line and "{" in line:
                try:
                    rec = json.loads(line[line.index("{"):])
                except ValueError:
                    continue
                q = str(rec.get("user_message", "")).strip()
                if len(q.split()) >= 3 and q not in seen:
                    seen.add(q)
                    out.append((q, float(rec.get("timestamp") or 0)))
    out.sort(key=lambda x: x[1])
    return out[-n:]


def rows(res) -> list[dict]:
    return [{"content": d or "", "metadata": m or {}, "score": s}
            for d, m, s in zip(res["documents"][0], res["metadatas"][0], res["distances"][0])]


def as_of(ts: float, results: list[dict]) -> list[dict]:
    """Keep only entries that existed when the query was asked: the test queries are past messages whose own
    exchange (and everything after it) is now in L3, which the live turn could not see."""
    return [r for r in results if float((r["metadata"] or {}).get("timestamp") or 0) < ts]


def parents(results) -> list[str]:
    return [(r["metadata"] or {}).get("message_id") or (r["metadata"] or {}).get("parent_id") for r in results]


async def run_variant(rr, q, cands, tok, expand=None):
    pairs_max = max((len(tok(q + " " + c["content"])["input_ids"]) for c in cands), default=0)
    t0 = time.perf_counter()
    ranked = await rr.rerank(q, cands, top_k=len(cands))
    ms = (time.perf_counter() - t0) * 1000
    kept = blended_gap_filter(ranked)
    if expand:
        kept = expand(kept)
    block, used = fit_search_results(kept, L3_BUDGET)
    from memory.budget import estimate_tokens
    return {"ms": ms, "pad": min(pairs_max, 512), "tokens": estimate_tokens(block), "used": used,
            "parents": parents(kept[:used]), "kept": kept}


async def main_async(a) -> int:
    import chromadb
    from chromadb.config import Settings
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copytree(a.source, Path(tmp) / "src")
        src = chromadb.PersistentClient(path=str(Path(tmp) / "src"), settings=Settings(anonymized_telemetry=False))
        main_col = src.get_collection(EPISODIC_COLLECTION_NAME)
        ch_client = chromadb.PersistentClient(path=a.chunks, settings=Settings(anonymized_telemetry=False))
        ch_col = ch_client.get_collection(CHUNK_COLLECTION_NAME)
        from memory.episodic.chunks import ChunkStore
        from memory.episodic.episodic import EpisodicMemory
        mem = EpisodicMemory(storage_path=str(Path(tmp) / "src"),
                             chunks=ChunkStore(path=a.chunks, collection=CHUNK_COLLECTION_NAME))
        hl_ms = []
        rr = CrossEncoderReranker()
        rr._load_and_prime()
        tok = rr._get_model().tokenizer
        qs = real_queries(a.queries)
        stats = {"before": [], "after": [], "highlights": []}
        agree1 = overlap3 = h_agree1 = h_overlap3 = 0
        h_diff = []
        for q, ts in qs:
            before = await run_variant(rr, q, as_of(ts, rows(main_col.query(query_texts=[q], n_results=60)))[:20], tok)
            seen, cands = set(), []
            for r in as_of(ts, rows(ch_col.query(query_texts=[q], n_results=120))):  # one chunk per parent
                pid = r["metadata"].get("message_id")
                if pid not in seen:
                    seen.add(pid)
                    cands.append(r)
            cands = cands[:20]

            def expand(kept):
                # whole-document candidates (no chunk found for them) have no parent_id and stay as they are
                ids = sorted({r["metadata"]["parent_id"] for r in kept if r["metadata"].get("parent_id")})
                got = main_col.get(ids=ids, include=["documents"]) if ids else {"ids": [], "documents": []}
                text = dict(zip(got["ids"], got["documents"]))
                return [dict(r, content=window_text(text.get(r["metadata"]["parent_id"], r["content"]),
                                                    r["metadata"].get("chunk_index", 0)))
                        if r["metadata"].get("parent_id") else r for r in kept]
            after = await run_variant(rr, q, cands, tok, expand=expand)
            # highlights through the REAL code path: EpisodicMemory.highlight before the rerank and
            # EpisodicMemory.expand_chunks after it (same as MemoryLayers.rerank in chunks_highlights mode)
            base = as_of(ts, rows(main_col.query(query_texts=[q], n_results=60)))[:20]
            t0 = time.perf_counter()
            h_cands = await mem.highlight(q, base)
            hl_ms.append((time.perf_counter() - t0) * 1000)
            hl = await run_variant(rr, q, h_cands, tok)  # rerank + gap filter on the chunks; expansion below
            hl["used_expanded"] = await mem.expand_chunks(hl.pop("kept"))
            from memory.budget import estimate_tokens as _et
            block, used = fit_search_results(hl["used_expanded"], L3_BUDGET)
            hl.update(tokens=_et(block), used=used, parents=parents(hl["used_expanded"][:used]))
            stats["before"].append(before)
            stats["after"].append(after)
            stats["highlights"].append(hl)
            if before["parents"][:1] and before["parents"][:1] == hl["parents"][:1]:
                h_agree1 += 1
            elif hl["parents"][:1]:
                h_diff.append(q)
            hb3, ha3 = set(before["parents"][:3]), set(hl["parents"][:3])
            h_overlap3 += len(hb3 & ha3) / max(1, len(hb3)) if hb3 else 1
            if before["parents"][:1] and before["parents"][:1] == after["parents"][:1]:
                agree1 += 1
            b3, a3 = set(before["parents"][:3]), set(after["parents"][:3])
            overlap3 += len(b3 & a3) / max(1, len(b3)) if b3 else 1
        n = len(qs)
        med = lambda k, v: st.median(x[k] for x in stats[v])
        print(f"queries: {n} (real user messages from goat2.log; vector search + rerank only)")
        for v in ("before", "after", "highlights"):
            print(f"  {v:10}: rerank median {med('ms', v):.0f} ms (p90 {sorted(x['ms'] for x in stats[v])[int(0.9*(n-1))]:.0f}) · "
                  f"padded pair median {med('pad', v):.0f} tok · L3 injected median {med('tokens', v):.0f} tok · "
                  f"entries median {med('used', v):.0f}")
        print(f"  chunks     vs before: top-1 same parent {agree1}/{n} ({agree1*100//max(1,n)}%) · top-3 overlap {overlap3/max(1,n)*100:.0f}%")
        print(f"  highlights vs before: top-1 same parent {h_agree1}/{n} ({h_agree1*100//max(1,n)}%) · top-3 overlap {h_overlap3/max(1,n)*100:.0f}%")
        print(f"  highlights chunk lookup (extra step before rerank): median {st.median(hl_ms):.0f} ms, "
              f"p90 {sorted(hl_ms)[int(0.9 * (len(hl_ms) - 1))]:.0f} ms")
        print(f"  highlights picked another top-1 for {len(h_diff)} queries (judge these by hand):")
        for q in h_diff[:10]:
            print("    -", " ".join(q.split())[:110])
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", default="./chroma_data")
    ap.add_argument("--chunks", default="./chroma_chunks")
    ap.add_argument("--queries", type=int, default=40)
    return asyncio.run(main_async(ap.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
