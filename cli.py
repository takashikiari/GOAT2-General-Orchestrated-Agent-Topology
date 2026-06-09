"""GOAT 2.0 interactive chat loop. Usage: python cli.py"""

from __future__ import annotations

import asyncio
import logging
import sys

from config.registry import ServiceRegistry
from supervisor.session import store_turn
from supervisor.supervisor import GoatSupervisor

# Create ServiceRegistry first to get settings
_registry = ServiceRegistry()

# Set global registry for tool handlers
from tools.registry_accessor import set_registry
set_registry(_registry)

logging.basicConfig(
    level=getattr(logging, _registry.settings.log_level.upper(), logging.INFO),
    format="%(asctime)s  %(name)-24s  %(levelname)s  %(message)s",
)
log = logging.getLogger("goat2.cli")

_SEP    = "═" * 62
_PROMPT = "\nyou> "


def _print_result(result) -> None:
    """Print summary and run stats."""
    print(f"\n{_SEP}\n{result.summary}\n{_SEP}")
    print(f"  tasks={len(result.results)}  |  {result.total_duration_s:.1f}s  |  ok={result.success}")


async def _read_line(loop: asyncio.AbstractEventLoop) -> str:
    """Read one line from stdin without blocking the event loop."""
    return await loop.run_in_executor(None, sys.stdin.readline)


async def chat_loop() -> None:
    """Interactive GOAT 2.0 session; supervisor and memory persist across turns."""
    sv = GoatSupervisor(registry=_registry)
    loop = asyncio.get_running_loop()
    turn = 0
    print("GOAT 2.0 — type your intent, 'exit' to quit.")
    try:
        while True:
            sys.stdout.write(_PROMPT)
            sys.stdout.flush()
            try:
                line = await _read_line(loop)
            except (EOFError, KeyboardInterrupt):
                print("\nBye.")
                break
            if not line:
                print("\nBye.")
                break
            intent = line.strip()
            if not intent:
                continue
            if intent.lower() in {"exit", "quit", "q"}:
                print("Bye.")
                break
            try:
                result = await sv.run(intent)
                _print_result(result)
                turn += 1
                await store_turn(_registry.memory_manager, turn, intent, result.summary)
            except Exception as exc:
                log.error("Run failed: %s", exc)
                print(f"\n[error] {exc}")
    finally:
        await sv.finalize_session()


def main() -> None:
    """Entry point."""
    asyncio.run(chat_loop())


if __name__ == "__main__":
    main()
