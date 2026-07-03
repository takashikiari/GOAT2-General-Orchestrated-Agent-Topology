"""benchmark.results — save, load, and export benchmark results.

Pure stdlib (``json``, ``csv``) with no ``orchestrator`` or ``memory`` imports.
``save`` writes a results dict to JSON; ``load`` reads it back; ``export_csv``
writes one row per test case for spreadsheet analysis. Accepted shapes:

* a single run: ``{"dataset": str, "results": [case, ...]}``
* a multi-run bundle: ``{"runs": [run, ...]}``
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from utils.logging.setup import get_logger

log = get_logger(__name__)

# Ordered CSV columns — one row per test case. ``extrasaction="ignore"`` drops
# any extra fields on the case dict, so adding fields does not break exports.
_CSV_FIELDS: tuple[str, ...] = (
    "dataset", "id", "name", "correct", "score", "match_method",
    "latency", "cache_hit", "prefetch_succeeded", "results_used",
    "tokens_injected", "tokens_l3", "source_tier", "error",
)

# Columns for the per-(dataset, run) summary CSV emitted by ``--runs`` mode.
_RUN_CSV_FIELDS: tuple[str, ...] = (
    "dataset", "run", "total_tests", "correct", "accuracy",
    "avg_latency", "cache_hit_rate", "prefetch_usefulness",
)


class ResultStorage:
    """Save and load benchmark results to/from disk."""

    @staticmethod
    def save(results: dict[str, Any], filename: str = "benchmark_results.json") -> None:
        """Save ``results`` to a JSON file (created or overwritten)."""
        path = Path(filename)
        path.write_text(json.dumps(results, default=str, indent=2))
        log.info("saved benchmark results to %s", path)

    @staticmethod
    def load(filename: str) -> dict[str, Any]:
        """Load a results dict from a JSON file."""
        return json.loads(Path(filename).read_text())

    @staticmethod
    def export_csv(results: dict[str, Any], filename: str = "benchmark_results.csv") -> None:
        """Export one row per test case to CSV for further analysis.

        ``results`` may be a single run or a ``{"runs": [...]}`` bundle. Cases
        missing a field yield an empty cell; extra case fields are ignored.
        """
        rows = ResultStorage._extract_rows(results)
        if not rows:
            log.warning("no test-case rows to export to %s", filename)
            return
        with open(filename, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(_CSV_FIELDS), extrasaction="ignore")
            writer.writeheader()
            for case in rows:
                writer.writerow({k: case.get(k, "") for k in _CSV_FIELDS})
        log.info("exported %d rows to %s", len(rows), filename)

    @staticmethod
    def _extract_rows(results: dict[str, Any]) -> list[dict]:
        """Flatten a single-run or multi-run bundle into a flat list of case dicts."""
        if isinstance(results.get("runs"), list):
            runs = results["runs"]
        else:
            runs = [results]
        rows: list[dict] = []
        for run in runs:
            dataset = run.get("dataset", "")
            for case in run.get("results", []) or []:
                merged = dict(case)
                merged.setdefault("dataset", dataset)
                rows.append(merged)
        return rows

    @staticmethod
    def export_runs_csv(bundle: dict[str, Any], filename: str = "benchmark_runs.csv") -> None:
        """Export one row per (dataset, run) from an aggregated ``--runs`` bundle.

        ``bundle`` is ``{"runs": [{"dataset", "per_run": [metric_dict, ...]}]}``.
        Each row is a single repetition's aggregate metrics for one dataset.
        """
        rows: list[dict] = []
        for run in bundle.get("runs", []) or []:
            dataset = run.get("dataset", "")
            for i, m in enumerate(run.get("per_run", []) or []):
                rows.append({
                    "dataset": dataset, "run": i + 1,
                    "total_tests": m.get("total_tests", ""),
                    "correct": m.get("correct", ""),
                    "accuracy": f"{float(m.get('accuracy', 0) or 0) * 100:.1f}",
                    "avg_latency": f"{float(m.get('avg_latency', 0) or 0):.2f}",
                    "cache_hit_rate": f"{float(m.get('cache_hit_rate', 0) or 0) * 100:.1f}",
                    "prefetch_usefulness": f"{float(m.get('prefetch_usefulness', 0) or 0) * 100:.1f}",
                })
        if not rows:
            log.warning("no per-run rows to export to %s", filename)
            return
        with open(filename, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(_RUN_CSV_FIELDS))
            writer.writeheader()
            writer.writerows(rows)
        log.info("exported %d run rows to %s", len(rows), filename)