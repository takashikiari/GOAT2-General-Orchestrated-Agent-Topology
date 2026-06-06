"""GOAT 2.0 interactive chat loop. Usage: python cli.py"""

from __future__ import annotations

import asyncio
import logging
import sys

from config.settings import settings
from memory.memory_manager import memory_manager
from memory.redis_backend import RedisBackend
from supervisor.session import store_turn
from supervisor.supervisor import GoatSupervisor

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
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
    b = RedisBackend()
    if await b.ping():
        memory_manager.working.backend = b
        print("Working memory: RedisBackend")
    else:
        print("Working memory: DictBackend (Redis unavailable)")
    sv   = GoatSupervisor(memory_manager=memory_manager)
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
                await store_turn(memory_manager, turn, intent, result.summary)
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
