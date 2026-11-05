"""tests.test_shell_tool — tools.goat_skills.shell's shell_run handler.

Regression test for a real incident: subprocess.run() was called directly
inside the async handler (no asyncio.to_thread), so every shell_run call
froze the ENTIRE bot process — not just the tool call — for the full
command duration, since the handler shares one event loop with Telegram
polling and the admin panel's HTTP server. Caught live: three chained
shell_run calls (10s + 10s + 30s-timeout) left curl against the admin
panel's own port getting zero response the whole time, even though the
TCP connection itself succeeded.
"""
from __future__ import annotations

import asyncio
import time

from tools.goat_skills.shell import build


class _FakeRegistry:
    pass


def _handler():
    return build(_FakeRegistry())[0].handler


async def test_shell_run_returns_command_output():
    handler = _handler()
    result = await handler("echo hello")
    assert "hello" in result


async def test_shell_run_reports_timeout():
    handler = _handler()
    result = await handler("sleep 5", timeout=1)
    assert "timed out after 1s" in result


async def test_shell_run_does_not_block_the_event_loop():
    """The real bug: a slow shell command must not freeze other concurrent
    coroutines (Telegram polling, the admin panel's HTTP server, ...).

    Runs a ticker task alongside a 0.5s shell command and checks whether the
    ticker made progress DURING the command's wall-clock window (not just
    that its own internal gaps look fine — a ticker that gets fully starved
    until the blocking call returns, then bursts through all its ticks
    afterward, would pass a naive gap check while still proving the loop
    was blocked the whole time). A blocked event loop lets zero ticks fall
    in that window; a free one lets roughly command_duration / tick_interval
    through.
    """
    handler = _handler()
    ticks: list[float] = []
    stop = asyncio.Event()

    async def ticker():
        while not stop.is_set():
            ticks.append(time.monotonic())
            await asyncio.sleep(0.05)

    ticker_task = asyncio.create_task(ticker())
    start = time.monotonic()
    await handler("sleep 0.5")
    end = time.monotonic()
    stop.set()
    await ticker_task

    ticks_during_call = [t for t in ticks if start < t < end]
    assert len(ticks_during_call) >= 5, (
        f"event loop appears blocked during the shell command: only "
        f"{len(ticks_during_call)} ticker ticks landed inside the "
        f"{end - start:.2f}s window"
    )
