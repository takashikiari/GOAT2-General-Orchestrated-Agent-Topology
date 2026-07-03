"""``python -m benchmark`` — CLI entry point for the GOAT 2.0 benchmark suite.

Examples:
    python3 -m benchmark --list
    python3 -m benchmark --dataset memory_recall
    python3 -m benchmark --dataset temporal --verbose
    python3 -m benchmark --all
    python3 -m benchmark --all --output results.json --csv results.csv
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from benchmark.datasets import list_datasets
from benchmark.metrics import AggregatedMetrics
from benchmark.results import ResultStorage
from benchmark.runner import BenchmarkRunner
from utils.logging.setup import get_logger

log = get_logger(__name__)

# Subsystems that log at INFO per turn and would drown out the report. Quieted
# to WARNING unless --verbose is set, so the benchmark output stays readable.
_NOISY_LOGGERS = (
    "orchestrator", "memory", "registry", "plugins", "tools",
    "telegram_interface", "openai", "httpx", "httpcore",
)


def _quiet_noisy_loggers(verbose: bool) -> None:
    """Silence per-turn subsystem INFO logs unless ``verbose`` is requested."""
    level = logging.INFO if verbose else logging.WARNING
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(level)


def _build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser for the benchmark CLI."""
    parser = argparse.ArgumentParser(prog="benchmark", description="Run GOAT 2.0 benchmarks.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--list", action="store_true", help="list built-in datasets and exit")
    mode.add_argument("--all", action="store_true", help="run every built-in dataset")
    mode.add_argument("--dataset", metavar="NAME", help="run a single dataset")
    parser.add_argument("--verbose", action="store_true", help="log each test case as it runs")
    parser.add_argument("--judge-llm", action="store_true",
                        help="use an LLM judge (extra LLM call per case) to score answers")
    parser.add_argument("--runs", type=int, default=1, metavar="N",
                        help="repeat each dataset N times and report mean ± std (default 1)")
    parser.add_argument("--output", metavar="FILE", help="save results as JSON to FILE")
    parser.add_argument("--csv", metavar="FILE", help="export per-case rows as CSV to FILE")
    return parser


async def _run(args: argparse.Namespace) -> int:
    """Dispatch the selected mode and print/save the report. Returns the exit code."""
    if args.list:
        for name in list_datasets():
            print(name)
        return 0
    _quiet_noisy_loggers(args.verbose)
    if not (args.all or args.dataset):
        print("error: pass --list, --all, or --dataset NAME", file=sys.stderr)
        return 2
    runner = BenchmarkRunner()
    if args.runs and args.runs > 1:
        return await _run_repeated(runner, args)
    if args.all:
        out = await runner.run_all(verbose=args.verbose, judge_llm=args.judge_llm)
        runs = out["runs"]
    else:
        runs = [await runner.run_dataset(args.dataset, verbose=args.verbose, judge_llm=args.judge_llm)]
    print(runner.report())
    if args.output or args.csv:
        bundle = _serialize(runs)
        if args.output:
            ResultStorage.save(bundle, args.output)
        if args.csv:
            ResultStorage.export_csv(bundle, args.csv)
    return 0


def _serialize(runs: list[dict]) -> dict:
    """Convert run dicts (carrying a ``BenchmarkMetrics`` object) to JSON form."""
    return {
        "runs": [
            {"dataset": r["dataset"], "metrics": r["metrics"].to_dict(), "results": r["results"]}
            for r in runs
        ]
    }


async def _run_repeated(runner: BenchmarkRunner, args: argparse.Namespace) -> int:
    """Run each selected dataset ``args.runs`` times and print mean ± std."""
    targets = list_datasets() if args.all else [args.dataset]
    bundle_runs: list[dict] = []
    lines: list[str] = []
    for name in targets:
        per_run: list[dict] = []
        for i in range(args.runs):
            run = await runner.run_dataset(name, verbose=args.verbose, judge_llm=args.judge_llm)
            per_run.append(run["metrics"].to_dict())
            if args.verbose:
                log.info("run %d/%d dataset=%s accuracy=%.1f%%",
                         i + 1, args.runs, name, per_run[-1]["accuracy"] * 100)
        agg = AggregatedMetrics.from_runs(per_run)
        lines.extend(agg.summary_lines(name))
        lines.append("")
        bundle_runs.append({"dataset": name, "aggregated": agg.to_dict(), "per_run": per_run})
    print("\n".join(lines).rstrip())
    bundle = {"runs": bundle_runs}
    if args.output:
        ResultStorage.save(bundle, args.output)
    if args.csv:
        ResultStorage.export_runs_csv(bundle, args.csv)
    return 0


def main() -> None:
    """Parse argv and run the selected benchmark mode."""
    args = _build_parser().parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()