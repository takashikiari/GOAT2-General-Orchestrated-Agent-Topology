"""scripts.run_real_data_benchmark — end-to-end real-data prefetch benchmark driver.

Wires together the pieces built for the real-data prefetch benchmark (spec
docs/superpowers/specs/2026-07-08-real-data-prefetch-benchmark-design.md):
mine (or load cached) ground-truth cases from a benchmark ChromaDB snapshot,
run the retrieval-only harness (prefetch_bench) and the full-cycle harness
(conversation_runner, via BenchmarkRunner.run_conversation) over those cases,
and print a combined report.

Prerequisites: chroma_data_benchmark/ must already exist — run
scripts/snapshot_episodic_for_benchmark.py first to create/refresh it.

Usage:
    python3 -m scripts.run_real_data_benchmark
    python3 -m scripts.run_real_data_benchmark --limit 5 --benchmark-path chroma_data_benchmark
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from benchmark.prefetch_bench import run_prefetch_benchmark
from benchmark.prefetch_metrics import PrefetchMetrics
from benchmark.real_data_mining import load_or_mine
from benchmark.runner import BenchmarkRunner

_DEFAULT_CACHE_PATH = Path("benchmark/data/real_recall_cases.json")
_DEFAULT_CONVERSATION_PATH = "chroma_data_benchmark_conversations"

__all__ = ["run_real_data_benchmark"]


def _build_chroma_client(path: str):
    """Construct a ChromaDB PersistentClient at ``path`` with telemetry disabled."""
    import chromadb
    import posthog as _posthog
    from chromadb.config import Settings
    _posthog.disabled = True
    _posthog.capture = lambda *a, **k: None  # type: ignore[assignment]
    return chromadb.PersistentClient(path=path, settings=Settings(anonymized_telemetry=False))


def _load_snapshot_entries(benchmark_path: str, collection_name: str) -> list[dict]:
    """Read every (id, document, metadata) row from the benchmark ChromaDB collection."""
    client = _build_chroma_client(benchmark_path)
    col = client.get_or_create_collection(collection_name)
    r = col.get(include=["documents", "metadatas"])
    ids = list(r.get("ids") or [])
    docs = list(r.get("documents") or [])
    metas = [dict(m or {}) for m in (r.get("metadatas") or [])]
    return [{"id": i, "content": d, "metadata": m} for i, d, m in zip(ids, docs, metas)]


async def _reseed_conversation_collection(
    reference_path: str, conversation_path: str, collection_name: str,
) -> int:
    """Copy the reference snapshot into the conversation-scratch path, fresh.

    Keeps conversation_runner's writes (L3 archive, store_memory) isolated from
    the reference collection prefetch_bench measures against — otherwise
    repeated benchmark runs accumulate query-text duplicates in the reference
    corpus that skew hit@K on later runs. Confirmed as the root cause of a lost
    hit@K case on real data (2026-07-08): a prior run's own conversation turns
    wrote near-duplicate copies of their query text into chroma_data_benchmark,
    which then outranked the real target on a later measurement.
    """
    from scripts.snapshot_episodic_for_benchmark import export_snapshot
    source_client = _build_chroma_client(reference_path)
    source_col = source_client.get_or_create_collection(collection_name)
    dest_client = _build_chroma_client(conversation_path)
    return await export_snapshot(source_col, dest_client, collection_name)


async def run_real_data_benchmark(cases: list[dict], layers, runner: BenchmarkRunner) -> dict:
    """Evaluate ``cases`` through both harnesses; returns ``{prefetch_metrics, conversations}``."""
    prefetch_metrics = await run_prefetch_benchmark(cases, layers)
    conversations = [await runner.run_conversation(case) for case in cases]
    return {"prefetch_metrics": prefetch_metrics, "conversations": conversations}


def _summary_lines(result: dict) -> list[str]:
    """Compact human-readable report combining prefetch + conversation results."""
    pm: PrefetchMetrics = result["prefetch_metrics"]
    conversations = result["conversations"]
    n = len(conversations)
    warm_served = sum(1 for c in conversations if c["warm"].get("warm_served"))
    grounded_warm = sum(1 for c in conversations if c["warm"]["groundedness"].get("grounded"))
    grounded_cold = sum(1 for c in conversations if c["cold"]["groundedness"].get("grounded"))
    hallucinated_warm = sum(
        len(c["warm"]["groundedness"].get("hallucinated_claims", [])) for c in conversations
    )
    hallucinated_cold = sum(
        len(c["cold"]["groundedness"].get("hallucinated_claims", [])) for c in conversations
    )
    lines = [
        "📊 REAL-DATA PREFETCH BENCHMARK",
        f"   Cases: {pm.total_cases}",
        "   -- Retrieval (prefetch_bench) --",
    ]
    for state in ("cold", "warm", "drift"):
        rank = pm.mean_rank_by_state.get(state)
        rank_str = f"{rank:.2f}" if rank is not None else "n/a"
        lines.append(
            f"   {state}: hit@K={pm.hit_rate_by_state.get(state, 0.0) * 100:.1f}%  "
            f"mean_rank={rank_str}  mechanisms={pm.mechanism_hit_counts_by_state.get(state, {})}"
        )
    lines.extend([
        "   -- Full-cycle (conversation_runner) --",
        f"   warm_served: {warm_served}/{n}",
        f"   grounded (warm): {grounded_warm}/{n}   grounded (cold): {grounded_cold}/{n}",
        f"   hallucinated claims (warm): {hallucinated_warm}   (cold): {hallucinated_cold}",
    ])
    return lines


async def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Run the real-data prefetch benchmark end-to-end.")
    ap.add_argument("--benchmark-path", default="chroma_data_benchmark",
                     help="reference benchmark ChromaDB path (default: chroma_data_benchmark) — "
                          "prefetch_bench measures against this; never written to by this driver")
    ap.add_argument("--conversation-path", default=_DEFAULT_CONVERSATION_PATH,
                     help="separate ChromaDB path for conversation_runner's writes "
                          f"(default: {_DEFAULT_CONVERSATION_PATH}), reseeded fresh from "
                          "--benchmark-path each run unless --skip-reseed is given")
    ap.add_argument("--skip-reseed", action="store_true",
                     help="reuse --conversation-path as-is instead of reseeding it from "
                          "--benchmark-path (faster, but conversation turns accumulate across runs)")
    ap.add_argument("--cache-path", default=str(_DEFAULT_CACHE_PATH),
                     help="mined-cases cache JSON path")
    ap.add_argument("--limit", type=int, default=None, help="cap the number of cases evaluated")
    ap.add_argument("--force-mine", action="store_true", help="re-mine even if the cache exists")
    args = ap.parse_args()

    from memory.config import EPISODIC_COLLECTION_NAME
    from registry.registry import ServiceRegistry

    reference_registry = ServiceRegistry(episodic_storage_path=args.benchmark_path)
    layers = reference_registry.memory_layers

    entries = _load_snapshot_entries(args.benchmark_path, EPISODIC_COLLECTION_NAME)
    print(f"loaded {len(entries)} entries from {args.benchmark_path!r}")

    cases = await load_or_mine(
        entries, reference_registry.llm_client, Path(args.cache_path),
        force=args.force_mine, limit=args.limit,
    )
    print(f"evaluating {len(cases)} mined case(s)")

    if not args.skip_reseed:
        count = await _reseed_conversation_collection(
            args.benchmark_path, args.conversation_path, EPISODIC_COLLECTION_NAME,
        )
        print(f"reseeded {count} rows -> {args.conversation_path!r} (conversation_runner scratch)")

    conversation_registry = ServiceRegistry(episodic_storage_path=args.conversation_path)
    runner = BenchmarkRunner(registry=conversation_registry)
    result = await run_real_data_benchmark(cases, layers, runner)
    print("\n".join(_summary_lines(result)))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(asyncio.run(main()))
