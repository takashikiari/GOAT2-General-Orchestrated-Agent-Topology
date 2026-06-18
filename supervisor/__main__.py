"""GOAT 2.0 CLI entry point — run supervisor from command line with optional JSON output.

PHASE 4 UPDATE: Now requires ServiceRegistry for dependency injection.
Legacy singleton fallback removed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from config.registry import ServiceRegistry
from supervisor.supervisor import GoatSupervisor
from utils.llm_utils import strip_dsml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-24s  %(levelname)s  %(message)s",
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="GOAT 2.0 — GoatSupervisor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='Example:\n  python -m supervisor "Build a REST API for a todo app"',
    )
    parser.add_argument("intent", nargs="?", help="User intent string")
    parser.add_argument("--verbose", action="store_true", help="Log each agent turn")
    parser.add_argument("--json",    action="store_true", help="Print full result as JSON")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if args.verbose:
        logging.getLogger("goat2").setLevel(logging.DEBUG)
    if not args.intent:
        print("Usage: python -m supervisor '<intent>'", file=sys.stderr)
        sys.exit(1)

    # Phase 4: ServiceRegistry is now required
    registry = ServiceRegistry()
    # Set global registry for tool handlers
    from tools.registry_accessor import set_registry
    set_registry(registry)
    supervisor = GoatSupervisor(registry=registry)
    result = asyncio.run(supervisor.run(args.intent))

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
        return

    # Strip DSML markers from summary before printing
    clean_summary = strip_dsml(result.summary)

    width = 64
    print("\n" + "═" * width)
    print("  GOAT 2.0 — RESULT")
    print("═" * width)
    print(clean_summary)
    print()
    print("─" * width)
    print(
        f"  Tasks : {len(result.results)}"
        f"  |  Duration : {result.total_duration_s:.1f}s"
        f"  |  Success : {result.success}"
    )
    print("─" * width)


if __name__ == "__main__":
    main()
