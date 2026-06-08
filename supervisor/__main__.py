"""GOAT 2.0 CLI entry point — run supervisor from command line with optional JSON output."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from config.settings import settings
from supervisor import run

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
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

    result = asyncio.run(run(args.intent))

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
        return

    width = 64
    print("\n" + "═" * width)
    print("  GOAT 2.0 — RESULT")
    print("═" * width)
    print(result.summary)
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
