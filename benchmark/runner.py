"""benchmark.runner — run benchmark datasets against a live GOAT orchestrator.

``BenchmarkRunner`` owns a lazily-built ``ServiceRegistry`` + ``Orchestrator``
(registry overridable). For each case it preloads L2 (or L3 for ``episodic_only``
cases), runs the orchestrator, diffs ``memory_analytics`` for per-turn
cache/prefetch/token stats, and scores via ``Evaluator``. All
orchestrator/registry/memory imports are lazy (inside methods), so importing
this module needs no services running. No singletons.
"""
from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING

from benchmark.datasets import get_dataset, list_datasets
from benchmark.evaluator import Evaluator
from benchmark.metrics import BenchmarkMetrics
from utils.logging.setup import get_logger

if TYPE_CHECKING:
    from registry.registry import ServiceRegistry

log = get_logger(__name__)


class BenchmarkRunner:
    """Run benchmark datasets against GOAT and collect per-turn metrics."""

    def __init__(self, registry: "ServiceRegistry | None" = None) -> None:
        """Build the orchestrator from ``registry`` (or a fresh one) with memory tools.

        All service imports are lazy so construction is cheap and side-effect
        free; physical tiers connect on first I/O, not at construction.
        """
        from orchestrator.orchestrator import Orchestrator  # lazy
        from registry.registry import ServiceRegistry  # lazy
        from tools.memory_promote import build_promote_memory_tool  # lazy
        from tools.memory_tools import build_search_memory_tool  # lazy
        from tools.memory_writer import build_store_memory_tool  # lazy

        self._registry = registry or ServiceRegistry()
        layers = self._registry.memory_layers
        self._orchestrator = Orchestrator(
            self._registry,
            tools=[
                build_search_memory_tool(layers),
                build_store_memory_tool(layers),
                build_promote_memory_tool(layers),
            ],
        )
        self._runs: list[dict] = []

    async def run_single(
        self, test_case: dict, chat_id: str | None = None, *,
        judge_llm: bool = False,
    ) -> dict:
        """Run one test case: preload memory, ask the query, score the response.

        A fresh ``chat_id`` is generated when none is given, so cases in a
        dataset do not see each other's L2 history. ``episodic_only`` cases are
        written to L3 and given an empty L2, forcing the prefetch/search path.
        ``repeat`` re-asks the query N times (the L2.5 search cache serves
        repeats after the first); correctness is scored on the final response.
        """
        chat_id = chat_id or f"bench-{uuid.uuid4().hex[:12]}"
        layers = self._registry.memory_layers
        analytics = self._registry.memory_analytics
        await self._preload(test_case, chat_id, layers)
        repeat = max(1, int(test_case.get("repeat", 1) or 1))
        per_run: list[dict] = []
        response, error = "", None
        for _ in range(repeat):
            before = _snapshot(analytics)
            t0 = time.time()
            try:
                response = await self._orchestrator.run(test_case["query"], chat_id)
            except Exception as exc:  # noqa: BLE001 — one failed case must not abort the run
                error = str(exc)
                response = ""
                log.warning("orchestrator.run failed case=%s: %s", test_case.get("id"), exc)
            latency = time.time() - t0
            per_run.append(_diff(before, _snapshot(analytics), latency, response, error))
        result = self._score(test_case, response, per_run, judge_llm)
        if test_case.get("episodic_only"):
            # L3-only path: verify the fact was actually retrievable from L3
            # (grounding), so a correct-but-ungrounded answer is flagged as a guess.
            result["grounded"] = await self._grounded(test_case, chat_id)
        await self._maybe_llm_judge(test_case, result, judge_llm)
        return result

    async def run_dataset(
        self, dataset_name: str, chat_id: str | None = None, *,
        judge_llm: bool = False, verbose: bool = False,
    ) -> dict:
        """Run a complete dataset and return ``{dataset, metrics, results}``.

        Resets the analytics aggregator first so the dataset's aggregates start
        clean; per-turn stats come from counter diffs, so they are independent
        of any prior runs.
        """
        cases = get_dataset(dataset_name)
        self._registry.memory_analytics.reset()
        results: list[dict] = []
        for case in cases:
            result = await self.run_single(case, chat_id=chat_id, judge_llm=judge_llm)
            result["dataset"] = dataset_name
            if verbose:
                log.info("case %s %s correct=%s latency=%.2fs",
                         result["id"], result["name"], result["correct"], result["latency"])
            results.append(result)
        metrics = BenchmarkMetrics.from_results(results)
        run = {"dataset": dataset_name, "metrics": metrics, "results": results}
        self._runs.append(run)
        return run

    async def run_all(self, *, judge_llm: bool = False, verbose: bool = False) -> dict:
        """Run every built-in dataset; returns ``{"runs": [run, ...]}``."""
        runs = [
            await self.run_dataset(name, judge_llm=judge_llm, verbose=verbose)
            for name in list_datasets()
        ]
        return {"runs": runs}

    def report(self) -> str:
        """Generate the human-readable report across all runs so far."""
        if not self._runs:
            return "No benchmark runs yet."
        lines: list[str] = []
        for run in self._runs:
            lines.extend(run["metrics"].summary_lines(run["dataset"]))
            lines.append("")
        return "\n".join(lines).rstrip()

    async def _preload(self, test_case: dict, chat_id: str, layers) -> None:
        """Load the case's conversation into L2, or L3-only for episodic cases."""
        conv = test_case.get("conversation", []) or []
        if test_case.get("episodic_only"):
            for msg in conv:
                await layers.store_episodic(
                    chat_id, f"{msg['role']}: {msg['content']}",
                    tags=["benchmark", test_case.get("id", "")],
                )
            await layers.save_working_context(chat_id, [])
            return
        messages = [
            {"role": m["role"], "content": m["content"], "timestamp": time.time()}
            for m in conv
        ]
        await layers.save_working_context(chat_id, messages)

    async def _grounded(self, test_case: dict, chat_id: str) -> bool:
        """Grounding check: the expected fact is retrievable from L3 (mirrors the
        orchestrator's thematic search; False on failure/absent; never raises)."""
        from memory.config import PREFETCH_MAX_RESULTS  # lazy — no module-level memory import

        expected = test_case.get("expected", "")
        if not expected:
            return True
        try:
            results, _, _ = await self._registry.memory_layers.search_episodic_with_cache(
                chat_id, test_case["query"], limit=PREFETCH_MAX_RESULTS,
            )
            return Evaluator.fact_in_results(results, expected)
        except Exception as exc:  # noqa: BLE001 — grounding failure must not abort
            log.warning("grounding retrieval failed case=%s: %s", test_case.get("id"), exc)
            return False

    def _score(
        self, test_case: dict, response: str, per_run: list[dict], judge_llm: bool,
    ) -> dict:
        """Build the per-test result dict: correctness, similarity, summed stats."""
        expected = test_case.get("expected", "")
        contains = test_case.get("expected_contains") or []
        # `expected`/fuzzy_match is the primary path (paraphrase-tolerant);
        # `expected_contains` is the multi-keyword fallback. ``grounded`` defaults
        # True for L2 cases (fact in injected history); episodic_only overrides it.
        if expected:
            correct = Evaluator.fuzzy_match(response, expected)
            method = "fuzzy"
        elif contains:
            correct = Evaluator.contains(response, contains)
            method = "contains"
        else:
            correct = False
            method = "none"
        score = Evaluator.semantic_similarity(response, expected or " ".join(contains))
        last = per_run[-1]
        n = max(1, len(per_run))
        return {
            "id": test_case.get("id"), "name": test_case.get("name"), "dataset": "",
            "query": test_case["query"], "expected": expected, "response": response,
            "correct": correct, "grounded": True, "score": round(score, 4), "match_method": method,
            "latency": round(last["latency"], 4),
            "cache_hit": any(r["cache_hit"] for r in per_run),
            "cache_miss": any(r["cache_miss"] for r in per_run),
            "prefetch_attempted": any(r["prefetch_attempted"] for r in per_run),
            "prefetch_succeeded": any(r["prefetch_succeeded"] for r in per_run),
            "prefetch_timeout": any(r["prefetch_timeout"] for r in per_run),
            "results_found": sum(r["results_found"] for r in per_run),
            "results_used": sum(r["results_used"] for r in per_run),
            "tokens_injected": int(sum(r["tokens_injected"] for r in per_run) / n),
            "tokens_l0_l1": int(sum(r["tokens_l0_l1"] for r in per_run) / n),
            "tokens_l2": int(sum(r["tokens_l2"] for r in per_run) / n),
            "tokens_l3": int(sum(r["tokens_l3"] for r in per_run) / n),
            "source_tier": last["source_tier"], "runs": len(per_run), "error": last["error"],
        }

    async def _maybe_llm_judge(self, test_case: dict, result: dict, judge_llm: bool) -> None:
        """Override the lexical verdict with an LLM judge when requested."""
        if not judge_llm or not (result["expected"] or test_case.get("expected_contains")):
            return
        expected = result["expected"] or " ".join(test_case.get("expected_contains") or [])
        verdict = await Evaluator.llm_judge(
            result["response"], expected, result["query"], self._registry.llm_client,
        )
        result["correct"] = bool(verdict["correct"])
        result["match_method"] = "llm_judge"


def _snapshot(analytics) -> dict:
    """Read the analytics aggregator's raw counters as a flat dict."""
    return {
        "cache_hits": analytics.cache_hits,
        "cache_misses": analytics.cache_misses,
        "pf_attempts": analytics.total_prefetch_attempts,
        "pf_successes": analytics.total_prefetch_successes,
        "pf_timeouts": analytics.total_prefetch_timeouts,
        "tok_inj": analytics.total_tokens_injected,
        "tok_l0l1": analytics.total_tokens_l0_l1,
        "tok_l2": analytics.total_tokens_l2,
        "tok_l3": analytics.total_tokens_l3,
        "res_found": analytics.total_results_found,
        "res_used": analytics.total_results_used,
        "tier_hits": dict(analytics.tier_hits),
    }


def _diff(before: dict, after: dict, latency: float, response: str, error: str | None) -> dict:
    """Compute one run's contribution by differencing two analytics snapshots."""
    tiers = set(after["tier_hits"]) | set(before["tier_hits"])
    new_tier = next(
        (k for k in tiers if after["tier_hits"].get(k, 0) - before["tier_hits"].get(k, 0) > 0),
        "",
    )
    return {
        "latency": latency, "response": response, "error": error,
        "cache_hit": bool(after["cache_hits"] - before["cache_hits"]),
        "cache_miss": bool(after["cache_misses"] - before["cache_misses"]),
        "prefetch_attempted": bool(after["pf_attempts"] - before["pf_attempts"]),
        "prefetch_succeeded": bool(after["pf_successes"] - before["pf_successes"]),
        "prefetch_timeout": bool(after["pf_timeouts"] - before["pf_timeouts"]),
        "results_found": after["res_found"] - before["res_found"],
        "results_used": after["res_used"] - before["res_used"],
        "tokens_injected": after["tok_inj"] - before["tok_inj"],
        "tokens_l0_l1": after["tok_l0l1"] - before["tok_l0l1"],
        "tokens_l2": after["tok_l2"] - before["tok_l2"],
        "tokens_l3": after["tok_l3"] - before["tok_l3"],
        "source_tier": new_tier,
    }